#
# Postgres implementations of every store the app had on disk.
#
# One module rather than SQL scattered through the feature code, because the
# point of the file store was that you could read the whole persistence layer in
# one sitting, and that property is worth keeping. Each function here mirrors the
# signature of the file-backed one it stands in for, so the call sites don't know
# which they're talking to.
#

import json
import time
import uuid
from typing import Optional

import db
import tenancy


def ORG() -> str:
    """The tenant this request belongs to. A function, not a constant — the
    constant was the whole reason every account shared one workspace."""
    return tenancy.org()


def _epoch(value) -> Optional[float]:
    """Postgres hands back datetimes; the API has always spoken epoch seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return value.timestamp()


# ----------------------------------------------------------------- agents ---
def agent_exists(agent_id: str) -> bool:
    return db.one(
        "select 1 as x from agents where id = %s and org_id = %s", (agent_id, ORG())
    ) is not None


def list_agents() -> list[dict]:
    rows = db.query(
        """select id, config, version, updated_at
             from agents where org_id = %s order by updated_at desc""",
        (ORG(),),
    )
    return [
        {
            "id": r["id"],
            "name": r["config"].get("name", r["id"]),
            "version": r["version"],
            "updated_at": _epoch(r["updated_at"]),
            "node_count": len(r["config"].get("nodes", [])),
        }
        for r in rows
    ]


def get_agent(agent_id: str) -> dict:
    r = db.one(
        """select id, config, version, label, source, ops, created_at, updated_at
             from agents where id = %s and org_id = %s""",
        (agent_id, ORG()),
    )
    if r is None:
        raise KeyError(agent_id)
    return {
        "id": r["id"],
        "config": r["config"],
        "version": r["version"],
        "label": r["label"],
        "source": r["source"],
        "ops": r["ops"],
        "created_at": _epoch(r["created_at"]),
        "updated_at": _epoch(r["updated_at"]),
    }


def agent_versions(agent_id: str) -> list[dict]:
    rows = db.query(
        """select v.version, v.config, v.label, v.source, v.ops, v.created_at
             from agent_versions v join agents a on a.id = v.agent_id
            where v.agent_id = %s and a.org_id = %s order by v.version desc""",
        (agent_id, ORG()),
    )
    return [
        {
            "version": r["version"],
            "created_at": _epoch(r["created_at"]),
            "label": r["label"],
            "source": r["source"],
            "ops": r["ops"],
            "node_count": len(r["config"].get("nodes", [])),
        }
        for r in rows
    ]


def agent_version_config(agent_id: str, version: int) -> dict:
    r = db.one(
        """select v.config from agent_versions v join agents a on a.id = v.agent_id
            where v.agent_id = %s and v.version = %s and a.org_id = %s""",
        (agent_id, version, ORG()),
    )
    if r is None:
        raise KeyError(f"{agent_id}@v{version}")
    return r["config"]


def commit_agent(
    agent_id: str, config: dict, version: int, label: str, source: str, ops: list
) -> dict:
    """Write the version row and move the pointer, in one transaction.

    Both halves or neither: a current pointer with no matching version row would
    make the history lie, and that history is the entire reason Copilot edits are
    safe to accept.

    The version number is allocated *here*, from max+1 inside the transaction,
    and the `version` argument is only a fallback for a brand-new agent. The
    caller can't be trusted with it: it computes the next number from a separate
    read, so two writes that interleave — or one that follows a re-run migration
    — pick the same number, and the losing row was previously dropped on
    conflict. That produced a pointer and a history row at the same version with
    different graphs, which is worse than either write failing.
    """
    with db.conn() as c:
        with c.cursor() as cur:
            # Lock the agent row first — `for update` can't be combined with an
            # aggregate, and locking the parent is what actually serialises two
            # concurrent commits against the same agent.
            cur.execute("select 1 from agents where id = %s for update", (agent_id,))
            cur.execute(
                "select coalesce(max(version), 0) from agent_versions where agent_id = %s",
                (agent_id,),
            )
            row = cur.fetchone()
            highest = row[0] if row else 0
            next_version = highest + 1 if highest else max(int(version or 1), 1)

            cur.execute(
                """insert into agents (id, org_id, name, config, version, label, source, ops, updated_at)
                   values (%s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, now())
                   on conflict (id) do update set
                     name = excluded.name, config = excluded.config,
                     version = excluded.version, label = excluded.label,
                     source = excluded.source, ops = excluded.ops, updated_at = now()""",
                (
                    agent_id, ORG(), config.get("name", agent_id), db.jsonb(config),
                    next_version, label, source, db.jsonb(ops),
                ),
            )
            cur.execute(
                """insert into agent_versions (agent_id, version, config, label, source, ops)
                   values (%s, %s, %s::jsonb, %s, %s, %s::jsonb)""",
                (agent_id, next_version, db.jsonb(config), label, source, db.jsonb(ops)),
            )
        c.commit()
    return get_agent(agent_id)


def import_version(
    agent_id: str, version: int, config: dict, label: str, source: str,
    ops: list, created_at=None,
) -> None:
    """Insert a historical version verbatim. Migration only.

    Separate from commit_agent because migration is the one caller that legitimately
    knows its own version numbers and timestamps, and must not have them reallocated.
    """
    db.execute(
        """insert into agent_versions (agent_id, version, config, label, source, ops, created_at)
           values (%s,%s,%s::jsonb,%s,%s,%s::jsonb, coalesce(to_timestamp(%s), now()))
           on conflict (agent_id, version) do update
             set created_at = excluded.created_at""",
        (agent_id, version, db.jsonb(config), label, source, db.jsonb(ops), created_at),
    )


def set_current(agent_id: str, config: dict, version: int, label: str, source: str, ops: list) -> None:
    """Point an agent at a version that already exists. Migration only."""
    db.execute(
        """insert into agents (id, org_id, name, config, version, label, source, ops, updated_at)
           values (%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb, now())
           on conflict (id) do update set
             name = excluded.name, config = excluded.config, version = excluded.version,
             label = excluded.label, source = excluded.source, ops = excluded.ops,
             updated_at = now()""",
        (agent_id, ORG(), config.get("name", agent_id), db.jsonb(config),
         version, label, source, db.jsonb(ops)),
    )


def delete_agent(agent_id: str) -> None:
    db.execute("delete from agents where id = %s and org_id = %s", (agent_id, ORG()))


# ------------------------------------------------------------- test cases ---
def load_cases(agent_id: str) -> list[dict]:
    return db.query(
        """select id, name, persona, assertions, max_turns, origin,
                  source_issue, source_call
             from test_cases where agent_id = %s and org_id = %s
            order by ordinal, created_at""",
        (agent_id, ORG()),
    )


def save_cases(agent_id: str, cases: list[dict]) -> None:
    """Replace the agent's case list, preserving the given order.

    Upsert-then-prune rather than delete-then-insert, so a case keeps its row —
    and therefore its created_at — across every edit of the suite around it.
    """
    ids = [c["id"] for c in cases]
    with db.conn() as c:
        with c.cursor() as cur:
            for i, case in enumerate(cases):
                cur.execute(
                    """insert into test_cases
                         (id, agent_id, org_id, name, persona, assertions, max_turns,
                          origin, source_issue, source_call, ordinal)
                       values (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s)
                       on conflict (id) do update set
                         name = excluded.name, persona = excluded.persona,
                         assertions = excluded.assertions, max_turns = excluded.max_turns,
                         origin = excluded.origin, source_issue = excluded.source_issue,
                         source_call = excluded.source_call, ordinal = excluded.ordinal""",
                    (
                        case["id"], agent_id, ORG(), case.get("name", "Untitled case"),
                        db.jsonb(case.get("persona") or {}),
                        db.jsonb(case.get("assertions") or []),
                        int(case.get("max_turns", 14)),
                        case.get("origin", "manual"),
                        case.get("source_issue", ""),
                        case.get("source_call", ""),
                        i,
                    ),
                )
            if ids:
                cur.execute(
                    """delete from test_cases
                        where agent_id = %s and org_id = %s and not (id = any(%s))""",
                    (agent_id, ORG(), ids),
                )
            else:
                cur.execute(
                    "delete from test_cases where agent_id = %s and org_id = %s",
                    (agent_id, ORG()),
                )
        c.commit()


# ------------------------------------------------------------------- runs ---
def load_last_run(agent_id: str) -> Optional[dict]:
    r = db.one(
        """select total, passed, failed, duration_s, started_at, results
             from test_runs where agent_id = %s and org_id = %s
            order by created_at desc limit 1""",
        (agent_id, ORG()),
    )
    if r is None:
        return None
    return {
        "agent_id": agent_id,
        "total": r["total"],
        "passed": r["passed"],
        "failed": r["failed"],
        "duration_s": float(r["duration_s"]),
        "started_at": r["started_at"],
        "results": r["results"],
    }


def save_run(agent_id: str, run: dict) -> None:
    db.execute(
        """insert into test_runs
             (agent_id, org_id, total, passed, failed, duration_s, started_at, results)
           values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
        (
            agent_id, ORG(), run.get("total", 0), run.get("passed", 0), run.get("failed", 0),
            run.get("duration_s", 0), run.get("started_at"), db.jsonb(run.get("results") or []),
        ),
    )


# ------------------------------------------------------------------ calls ---
CALL_COLUMNS = """id, source, outcome, duration_s, flagged_by, turns, path, collected,
                  agent_version, from_number, to_number, provider_sid, recording_url,
                  metadata, created_at"""


def _call_row(r: dict) -> dict:
    return {
        "id": r["id"],
        "source": r["source"],
        "outcome": r["outcome"],
        "duration_s": float(r["duration_s"]),
        "flagged_by": r["flagged_by"],
        "turns": r["turns"],
        "path": r["path"],
        "collected": r["collected"],
        "agent_version": r["agent_version"],
        "from_number": r["from_number"],
        "to_number": r["to_number"],
        "provider_sid": r["provider_sid"],
        "recording_url": r["recording_url"],
        "metadata": r["metadata"],
        "created_at": _epoch(r["created_at"]),
    }


def load_calls(agent_id: str, limit: int = 500) -> list[dict]:
    rows = db.query(
        f"""select {CALL_COLUMNS} from calls where agent_id = %s and org_id = %s
             order by created_at desc limit %s""",
        (agent_id, ORG(), limit),
    )
    return [_call_row(r) for r in rows]


def load_workspace_calls(limit: int = 500) -> list[dict]:
    """Every call in the workspace, whichever agent took it.

    History is a record of what happened to *you*, not to whichever agent
    happens to be selected in the switcher. Scoping it to the selection meant
    ringing your own number and then finding an empty list, because the agent
    that answers is the *active* one and those are chosen independently.
    """
    rows = db.query(
        """select c.id, c.source, c.outcome, c.duration_s, c.flagged_by, c.turns,
                  c.path, c.collected, c.agent_version, c.from_number, c.to_number,
                  c.provider_sid, c.recording_url, c.metadata, c.created_at,
                  c.agent_id, a.config->>'name' as agent_name
             from calls c join agents a on a.id = c.agent_id
            where c.org_id = %s
            order by c.created_at desc limit %s""",
        (ORG(), limit),
    )
    return [
        {**_call_row(r), "agent_id": r["agent_id"], "agent_name": r["agent_name"]}
        for r in rows
    ]


def get_call(agent_id: str, call_id: str) -> Optional[dict]:
    r = db.one(
        f"select {CALL_COLUMNS} from calls where agent_id = %s and id = %s and org_id = %s",
        (agent_id, call_id, ORG()),
    )
    return _call_row(r) if r else None


def save_call(agent_id: str, call: dict) -> dict:
    db.execute(
        """insert into calls
             (id, agent_id, org_id, source, outcome, duration_s, flagged_by, turns, path,
              collected, agent_version, from_number, to_number, provider_sid,
              recording_url, metadata)
           values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s::jsonb)
           on conflict (id) do update set
             outcome = excluded.outcome, duration_s = excluded.duration_s,
             turns = excluded.turns, path = excluded.path, collected = excluded.collected,
             recording_url = excluded.recording_url, metadata = excluded.metadata""",
        (
            call["id"], agent_id, ORG(), call.get("source", "seed"),
            call.get("outcome", "unknown"), call.get("duration_s", 0),
            call.get("flagged_by", ""), db.jsonb(call.get("turns") or []),
            db.jsonb(call.get("path") or []), db.jsonb(call.get("collected") or {}),
            call.get("agent_version"), call.get("from_number", ""),
            call.get("to_number", ""), call.get("provider_sid", ""),
            call.get("recording_url", ""), db.jsonb(call.get("metadata") or {}),
        ),
    )
    return call


def save_calls(agent_id: str, calls: list[dict]) -> None:
    for call in calls:
        save_call(agent_id, call)


# ----------------------------------------------------------------- issues ---
ISSUE_COLUMNS = """id, title, description, severity, status, call_count,
                   affected_nodes, evidence, suggested_fix"""


def load_issues(agent_id: str) -> list[dict]:
    return db.query(
        f"""select {ISSUE_COLUMNS} from issues where agent_id = %s and org_id = %s
             order by created_at desc""",
        (agent_id, ORG()),
    )


def save_issues(agent_id: str, issues: list[dict]) -> None:
    with db.conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "delete from issues where agent_id = %s and org_id = %s", (agent_id, ORG())
            )
            for issue in issues:
                cur.execute(
                    """insert into issues
                         (id, agent_id, org_id, title, description, severity, status,
                          call_count, affected_nodes, evidence, suggested_fix)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)""",
                    (
                        issue["id"], agent_id, ORG(), issue.get("title", ""),
                        issue.get("description", ""), issue.get("severity", "medium"),
                        issue.get("status", "open"), issue.get("call_count", 0),
                        db.jsonb(issue.get("affected_nodes") or []),
                        db.jsonb(issue.get("evidence") or []),
                        issue.get("suggested_fix", ""),
                    ),
                )
        c.commit()


def set_issue_status(agent_id: str, issue_id: str, status: str) -> list[dict]:
    db.execute(
        "update issues set status = %s where agent_id = %s and id = %s and org_id = %s",
        (status, agent_id, issue_id, ORG()),
    )
    return load_issues(agent_id)


# -------------------------------------------------------------- decisions ---
def load_decisions(agent_id: str) -> list[dict]:
    rows = db.query(
        """select kind, summary, reason, created_at from decisions
            where agent_id = %s and org_id = %s order by created_at""",
        (agent_id, ORG()),
    )
    return [
        {"at": _epoch(r["created_at"]), "kind": r["kind"],
         "summary": r["summary"], "reason": r["reason"]}
        for r in rows
    ]


def record_decision(agent_id: str, kind: str, summary: str, reason: str = "") -> dict:
    db.execute(
        "insert into decisions (agent_id, org_id, kind, summary, reason) values (%s,%s,%s,%s,%s)",
        (agent_id, ORG(), kind, summary.strip(), reason.strip()),
    )
    return {"at": time.time(), "kind": kind, "summary": summary, "reason": reason}


# ------------------------------------------------------------------- jobs ---
def new_job(kind: str, agent_id: Optional[str] = None) -> str:
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    db.execute(
        """insert into jobs (id, agent_id, kind, status, progress, started_at)
           values (%s, %s, %s, 'running', '{"done":0,"total":0}'::jsonb, %s)""",
        (job_id, agent_id, kind, time.time()),
    )
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    r = db.one(
        """select id, kind, status, progress, partial, status_text, result, error, started_at
             from jobs where id = %s""",
        (job_id,),
    )
    return dict(r) if r else None


def update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    sets, params = [], []
    for key, value in fields.items():
        if key in ("progress", "partial", "result"):
            sets.append(f"{key} = %s::jsonb")
            params.append(db.jsonb(value))
        else:
            sets.append(f"{key} = %s")
            params.append(value)
    params.append(job_id)
    db.execute(f"update jobs set {', '.join(sets)} where id = %s", tuple(params))


def append_partial(job_id: str, item: dict) -> None:
    db.execute(
        "update jobs set partial = partial || %s::jsonb where id = %s",
        (db.jsonb([item]), job_id),
    )


def prune_jobs(keep: int = 200) -> None:
    db.execute(
        """delete from jobs where id in (
             select id from jobs order by created_at desc offset %s)""",
        (keep,),
    )
