#
# Background job state, moved out of the process.
#
# This was a module-level dict, which quietly meant the whole app could only
# ever run one worker: a job started on worker A is invisible to a poll that
# lands on worker B, so the UI hangs on a spinner forever. That is the first
# thing that breaks when this stops being a demo.
#
# The shape is deliberately unchanged — `JOBS[job_id]["status"] = "done"` still
# works — because job bookkeeping is threaded through half the endpoints and a
# rewrite of all of it would be a lot of risk for no behaviour. Writes go
# through to Postgres; reads prefer the local copy and fall back to the table,
# which is what makes a poll on another worker resolve.
#

import time
import uuid
from typing import Any, Iterator, MutableMapping, Optional

import db

PERSISTED = ("status", "progress", "partial", "status_text", "result", "error")


class Job(dict):
    """A job record that writes each change through to the jobs table."""

    def __init__(self, job_id: str, data: dict, persist: bool):
        super().__init__(data)
        self._id = job_id
        self._persist = persist

    def __setitem__(self, key: str, value: Any) -> None:
        super().__setitem__(key, value)
        if self._persist and key in PERSISTED:
            try:
                import repo

                repo.update_job(self._id, **{key: value})
            except Exception:
                # Losing the durable copy must never fail the job itself; the
                # in-process record is still correct for this worker.
                pass

    def update(self, *args, **kwargs) -> None:  # type: ignore[override]
        merged = dict(*args, **kwargs)
        for key, value in merged.items():
            self[key] = value

    def append_partial(self, item: dict) -> None:
        """Add a streamed result. Reassigns rather than mutating in place, so
        the write-through actually fires."""
        self["partial"] = list(self.get("partial") or []) + [item]


class JobStore(MutableMapping):
    """Dict-shaped, Postgres-backed when a database is configured."""

    def __init__(self) -> None:
        self._local: dict[str, Job] = {}

    @property
    def _persist(self) -> bool:
        return db.enabled()

    def new(self, kind: str, agent_id: Optional[str] = None) -> str:
        job_id = (
            self._new_persisted(kind, agent_id)
            if self._persist
            else f"job_{uuid.uuid4().hex[:10]}"
        )
        self._local[job_id] = Job(
            job_id,
            {
                "id": job_id,
                "kind": kind,
                "status": "running",
                "progress": {"done": 0, "total": 0},
                "partial": [],
                "status_text": "",
                "result": None,
                "error": "",
                "started_at": time.time(),
            },
            self._persist,
        )
        self._prune()
        return job_id

    def _new_persisted(self, kind: str, agent_id: Optional[str]) -> str:
        import repo

        return repo.new_job(kind, agent_id)

    def _prune(self) -> None:
        if len(self._local) > 200:
            for old in sorted(self._local.values(), key=lambda j: j["started_at"])[:80]:
                self._local.pop(old["id"], None)
            if self._persist:
                try:
                    import repo

                    repo.prune_jobs()
                except Exception:
                    pass

    def __getitem__(self, job_id: str) -> Job:
        job = self._local.get(job_id)
        if job is not None:
            return job
        if self._persist:
            import repo

            row = repo.get_job(job_id)
            if row:
                row.setdefault("started_at", time.time())
                job = Job(job_id, row, True)
                self._local[job_id] = job
                return job
        raise KeyError(job_id)

    def get(self, job_id: str, default=None):  # type: ignore[override]
        try:
            return self[job_id]
        except KeyError:
            return default

    def __setitem__(self, job_id: str, value: dict) -> None:
        self._local[job_id] = (
            value if isinstance(value, Job) else Job(job_id, value, self._persist)
        )

    def __delitem__(self, job_id: str) -> None:
        self._local.pop(job_id, None)

    def __iter__(self) -> Iterator[str]:
        return iter(self._local)

    def __len__(self) -> int:
        return len(self._local)


JOBS = JobStore()
