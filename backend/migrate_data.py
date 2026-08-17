"""Move the on-disk store into Postgres.

Idempotent and additive: it upserts by primary key and never deletes, so running
it twice is a no-op and running it after some work has already happened in
Postgres won't clobber that work. The files are left exactly where they are —
STORE_BACKEND=files still reads them, which is the only reason this is a safe
thing to run against real data.

    python migrate_data.py            # apply schema, then import
    python migrate_data.py --dry-run  # report what would move
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import db  # noqa: E402
import repo  # noqa: E402
import tenancy  # noqa: E402

tenancy.use_default_workspace()

DATA = Path(__file__).parent / "data"
DRY = "--dry-run" in sys.argv


def read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"    ! could not read {path.name}: {exc}")
        return None


def migrate_agents() -> list[str]:
    ids = []
    root = DATA / "agents"
    if not root.exists():
        return ids
    for folder in sorted(root.iterdir()):
        current = folder / "current.json"
        if not current.exists():
            continue
        rec = read(current)
        if not rec:
            continue
        agent_id = folder.name
        ids.append(agent_id)

        versions = sorted((folder / "versions").glob("*.json")) if (folder / "versions").exists() else []
        print(f"  agent {agent_id}: v{rec.get('version')} + {len(versions)} versions")
        if DRY:
            continue

        # Postgres wins once it's ahead. Re-running this after real work has
        # happened must not drag the agent back to whatever the files last said —
        # the files stop being updated the moment STORE_BACKEND flips, so they
        # are stale by definition, and silently reverting someone's production
        # graph is the worst thing a migration can do.
        live = repo.get_agent(agent_id) if repo.agent_exists(agent_id) else None
        if live and live["version"] > rec.get("version", 1):
            print(f"    Postgres is ahead (v{live['version']} > v{rec['version']}) — leaving it alone")
            continue

        # History first, verbatim, then the pointer. Original timestamps are
        # carried across; defaulting them to now() would stamp the whole
        # back-catalogue with the migration time and destroy the one thing
        # history is for — knowing when a change was made.
        for vpath in versions:
            v = read(vpath)
            if not v:
                continue
            repo.import_version(
                agent_id, v["version"], v["config"], v.get("label", ""),
                v.get("source", "manual"), v.get("ops", []),
                v.get("created_at") or v.get("updated_at"),
            )
        repo.import_version(
            agent_id, rec.get("version", 1), rec["config"], rec.get("label", ""),
            rec.get("source", "manual"), rec.get("ops", []),
            rec.get("created_at") or rec.get("updated_at"),
        )
        repo.set_current(
            agent_id, rec["config"], rec.get("version", 1),
            rec.get("label", ""), rec.get("source", "manual"), rec.get("ops", []),
        )

        decisions = folder / "decisions.json"
        if decisions.exists():
            entries = read(decisions) or []
            print(f"    decisions: {len(entries)}")
            if not DRY:
                existing = {(d["kind"], d["summary"]) for d in repo.load_decisions(agent_id)}
                for e in entries:
                    if (e["kind"], e["summary"]) not in existing:
                        repo.record_decision(agent_id, e["kind"], e["summary"], e.get("reason", ""))
    return ids


def migrate_cases(agent_ids: list[str]) -> None:
    folder = DATA / "tests"
    if not folder.exists():
        return
    for path in sorted(folder.glob("*.json")):
        agent_id = path.stem
        if agent_id not in agent_ids:
            print(f"  skipping tests for unknown agent '{agent_id}'")
            continue
        cases = (read(path) or {}).get("cases", [])
        print(f"  tests {agent_id}: {len(cases)} cases")
        if not DRY and cases:
            # Merge rather than replace: cases already in Postgres win on order.
            existing = {c["id"]: c for c in repo.load_cases(agent_id)}
            merged = list(existing.values()) + [c for c in cases if c["id"] not in existing]
            repo.save_cases(agent_id, merged)


def migrate_runs(agent_ids: list[str]) -> None:
    folder = DATA / "runs"
    if not folder.exists():
        return
    for path in sorted(folder.glob("*.json")):
        agent_id = path.stem
        if agent_id not in agent_ids:
            continue
        run = read(path)
        if not run:
            continue
        print(f"  run {agent_id}: {run.get('passed')}/{run.get('total')}")
        if not DRY and repo.load_last_run(agent_id) is None:
            repo.save_run(agent_id, run)


def migrate_calls(agent_ids: list[str]) -> None:
    folder = DATA / "calls"
    if not folder.exists():
        return
    for path in sorted(folder.glob("*.json")):
        agent_id = path.stem
        if agent_id not in agent_ids:
            continue
        calls = (read(path) or {}).get("calls", [])
        print(f"  calls {agent_id}: {len(calls)}")
        if DRY:
            continue
        for call in calls:
            call.setdefault("source", "seed")
            repo.save_call(agent_id, call)


def migrate_issues(agent_ids: list[str]) -> None:
    folder = DATA / "issues"
    if not folder.exists():
        return
    for path in sorted(folder.glob("*.json")):
        agent_id = path.stem
        if agent_id not in agent_ids:
            continue
        issues = (read(path) or {}).get("issues", [])
        print(f"  issues {agent_id}: {len(issues)}")
        if not DRY and issues:
            existing = {i["id"] for i in repo.load_issues(agent_id)}
            fresh = [i for i in issues if i["id"] not in existing]
            if fresh:
                repo.save_issues(agent_id, repo.load_issues(agent_id) + fresh)


def main() -> None:
    if not db.DB_URL:
        sys.exit("SUPABASE_DB_URL is not set — nothing to migrate into.")
    print(f"target: {db.DB_URL.split('@')[-1].split('?')[0]}")
    print(f"mode:   {'DRY RUN' if DRY else 'apply'}\n")

    if not DRY:
        print("schema:")
        db.migrate()

    print("\nagents:")
    ids = migrate_agents()
    print("\ntests:");  migrate_cases(ids)
    print("\nruns:");   migrate_runs(ids)
    print("\ncalls:");  migrate_calls(ids)
    print("\nissues:"); migrate_issues(ids)

    print("\nrow counts:")
    for table, n in db.table_counts().items():
        print(f"  {table:16} {n}")


if __name__ == "__main__":
    main()
