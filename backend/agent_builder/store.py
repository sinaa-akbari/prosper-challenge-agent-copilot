#
# Agent storage — versioned, file-backed, git-diffable.
#
# Every save writes a new immutable version alongside the current config, with
# the ops and rationale that produced it. That history is what makes Copilot
# edits safe to accept: anything it does can be read back and reverted in one
# click, so "let the AI edit my production agent" stops being a leap of faith.
#
# Files, not a database: an agent is a small JSON document, and keeping it on
# disk means you can diff it, commit it, and hand-edit it when you need to.
#

import json
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
AGENTS_DIR = DATA_DIR / "agents"

# Serialises version allocation in the file-backed path. See AgentStore.save.
_SAVE_LOCK = threading.Lock()


def repo():
    """Imported lazily: repo imports db, and db must not be a hard dependency
    of the file-backed path."""
    import repo as _repo

    return _repo


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "agent"
    return base[:40]


def _now() -> float:
    return time.time()


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class AgentStore:
    """CRUD + version history for agents on disk."""

    def __init__(self, root: Path = AGENTS_DIR):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- paths -------------------------------------------------------------
    def _dir(self, agent_id: str) -> Path:
        return self.root / agent_id

    def _current(self, agent_id: str) -> Path:
        return self._dir(agent_id) / "current.json"

    def _versions_dir(self, agent_id: str) -> Path:
        return self._dir(agent_id) / "versions"

    def exists(self, agent_id: str) -> bool:
        if db.enabled():
            return repo().agent_exists(agent_id)
        return self._current(agent_id).exists()

    # ---- reads -------------------------------------------------------------
    def list_agents(self) -> list[dict]:
        if db.enabled():
            return repo().list_agents()
        out = []
        for d in sorted(self.root.iterdir()) if self.root.exists() else []:
            if not (d / "current.json").exists():
                continue
            rec = _read(d / "current.json")
            out.append(
                {
                    "id": d.name,
                    "name": rec["config"].get("name", d.name),
                    "version": rec.get("version", 1),
                    "updated_at": rec.get("updated_at"),
                    "node_count": len(rec["config"].get("nodes", [])),
                }
            )
        return sorted(out, key=lambda a: a.get("updated_at") or 0, reverse=True)

    def get(self, agent_id: str) -> dict:
        """Return the full record: {id, config, version, updated_at, ...}."""
        if db.enabled():
            return repo().get_agent(agent_id)
        if not self.exists(agent_id):
            raise KeyError(agent_id)
        rec = _read(self._current(agent_id))
        rec["id"] = agent_id
        return rec

    def get_config(self, agent_id: str) -> dict:
        return self.get(agent_id)["config"]

    def versions(self, agent_id: str) -> list[dict]:
        if db.enabled():
            return repo().agent_versions(agent_id)
        vdir = self._versions_dir(agent_id)
        if not vdir.exists():
            return []
        out = []
        for f in sorted(vdir.glob("*.json"), reverse=True):
            rec = _read(f)
            out.append(
                {
                    "version": rec["version"],
                    "created_at": rec["created_at"],
                    "label": rec.get("label", ""),
                    "source": rec.get("source", "manual"),
                    "ops": rec.get("ops", []),
                    "node_count": len(rec["config"].get("nodes", [])),
                }
            )
        return out

    def version_config(self, agent_id: str, version: int) -> dict:
        if db.enabled():
            return repo().agent_version_config(agent_id, version)
        return _read(self._versions_dir(agent_id) / f"{version:04d}.json")["config"]

    # ---- writes ------------------------------------------------------------
    def create(self, config: dict, agent_id: Optional[str] = None, label: str = "Created") -> dict:
        agent_id = agent_id or f"{_slug(config.get('name', 'agent'))}-{uuid.uuid4().hex[:6]}"
        if self.exists(agent_id):
            raise ValueError(f"Agent '{agent_id}' already exists.")
        return self._commit(agent_id, config, version=1, label=label, source="manual", ops=[])

    def save(
        self,
        agent_id: str,
        config: dict,
        label: str = "Edited",
        source: str = "manual",
        ops: Optional[list] = None,
    ) -> dict:
        """Write a new version and make it current.

        Reading the current version and then writing version+1 is a race, and two
        writers that interleave used to overwrite each other's history. Postgres
        settles this itself by allocating the number inside the transaction; the
        file store has no such thing, so it takes a lock. Process-local only,
        which is all the file backend ever needs — it exists for single-process
        local work, and anything multi-worker is on Postgres by definition.
        """
        with _SAVE_LOCK:
            current = self.get(agent_id) if self.exists(agent_id) else None
            version = (current["version"] + 1) if current else 1
            return self._commit(agent_id, config, version, label, source, ops or [])

    def revert(self, agent_id: str, version: int) -> dict:
        """Roll back by writing the old config forward as a new version.

        History stays append-only — reverting is itself an event you can undo.
        """
        old = self.version_config(agent_id, version)
        return self.save(
            agent_id, old, label=f"Reverted to v{version}", source="revert", ops=[]
        )

    def delete(self, agent_id: str) -> None:
        if db.enabled():
            repo().delete_agent(agent_id)
            return
        import shutil

        if self.exists(agent_id):
            shutil.rmtree(self._dir(agent_id))

    def _commit(
        self, agent_id: str, config: dict, version: int, label: str, source: str, ops: list
    ) -> dict:
        if db.enabled():
            return repo().commit_agent(agent_id, config, version, label, source, ops)
        record = {
            "id": agent_id,
            "config": config,
            "version": version,
            "created_at": _now(),
            "updated_at": _now(),
            "label": label,
            "source": source,
            "ops": ops,
        }
        _write(self._versions_dir(agent_id) / f"{version:04d}.json", record)
        _write(self._current(agent_id), record)
        return record


store = AgentStore()
