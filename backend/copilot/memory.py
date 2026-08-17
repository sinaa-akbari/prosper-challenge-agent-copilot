#
# Decisions the engineer already accepted, so the Copilot stops arguing with
# its past self.
#
# Every Copilot call starts from a blank conversation and the current graph. That
# is fine for "add a node" and quietly corrosive for anything that was settled by
# a judgement call, because the reasoning behind a judgement call leaves no trace
# in the graph. The escape hatch that requires nothing looks under-specified. The
# test that was retired for being unsatisfiable looks like missing coverage. So
# the next session helpfully puts them back.
#
# This is the smallest thing that prevents it: an append-only log of what was
# accepted and why, replayed into the prompt. Not a knowledge base — a short list
# of decisions with reasons, which is exactly what a colleague returning to a
# codebase reads first.
#

import json
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Enough to carry the standing decisions, few enough that they stay read. Older
# entries stay on disk; only the tail is replayed.
MAX_REPLAYED = 14


def _path(agent_id: str) -> Path:
    return DATA_DIR / "agents" / agent_id / "decisions.json"


def load_decisions(agent_id: str) -> list[dict]:
    if db.enabled():
        import repo

        return repo.load_decisions(agent_id)
    path = _path(agent_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt log must never take the Copilot down with it; losing the
        # memory is a much smaller failure than losing the session.
        return []


def record(agent_id: str, kind: str, summary: str, reason: str = "") -> dict:
    """Append one decision. `kind` is 'change' | 'retired_test' | 'added_test'."""
    if db.enabled():
        import repo

        return repo.record_decision(agent_id, kind, summary, reason)
    entry = {
        "at": time.time(),
        "kind": kind,
        "summary": summary.strip(),
        "reason": reason.strip(),
    }
    path = _path(agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = load_decisions(agent_id)
    entries.append(entry)
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return entry


def record_acceptance(
    agent_id: str,
    label: str,
    reply: str,
    ops: Optional[list] = None,
    retired: Optional[list] = None,
    added: Optional[list] = None,
) -> None:
    """Log an accepted proposal: the change, and any test the engineer retired.

    The reply is the reasoning, so it's what gets kept — an op list can be
    re-derived from the version history, but *why* it was the right op cannot.
    """
    if ops:
        record(agent_id, "change", label, reply)
    for t in retired or []:
        record(
            agent_id,
            "retired_test",
            f"Retired the test '{t.get('name', '?')}'",
            t.get("reason", ""),
        )
    for t in added or []:
        record(agent_id, "added_test", f"Added the test '{t.get('name', '?')}'")


def decisions_context(agent_id: str) -> str:
    """Replay the standing decisions into the prompt."""
    entries = load_decisions(agent_id)
    if not entries:
        return ""

    retired = [e for e in entries if e["kind"] == "retired_test"]
    others = [e for e in entries if e["kind"] != "retired_test"][-MAX_REPLAYED:]

    parts = [
        "## Decisions already made on this agent\n",
        "Accepted by the engineer in earlier sessions. Treat them as settled: "
        "don't undo one without saying you're undoing it and why the reason no "
        "longer holds.\n",
    ]
    for e in others:
        parts.append(f"- {e['summary']}")
        if e["reason"]:
            parts.append(f"    because: {e['reason'][:400]}")

    if retired:
        # Called out separately because the failure mode is specific: a retired
        # test looks exactly like a coverage gap to anyone who wasn't there.
        parts.append(
            "\nThese tests were retired deliberately. **Do not propose them again** "
            "under the same or a reworded name:"
        )
        for e in retired:
            parts.append(f"- {e['summary']}")
            if e["reason"]:
                parts.append(f"    because: {e['reason'][:400]}")
    return "\n".join(parts) + "\n"
