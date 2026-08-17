#
# Issue mining — production calls in, ranked and evidenced problems out.
#
# The brief says detecting the issues is itself a burden, and that matches what
# the work actually looks like: nobody reads a thousand transcripts, so problems
# surface only when a client complains, which means the slowest and most
# expensive channel is the primary one.
#
# So the loop starts here rather than at the chat box. Calls are read in bulk and
# clustered into a small number of recurring failures, each one ranked, tied to a
# node in the graph, and quoted from the calls that show it. A cluster of nine
# calls is a different object from nine anecdotes: it is prioritisable, it is
# arguable, and — because every issue carries its evidence — it is a complete
# instruction for the Copilot without anyone having to restate it.
#

import json
import time
import uuid
from pathlib import Path
from typing import Optional

import db
from llm import LLMClient, copilot_llm

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CALLS_DIR = DATA_DIR / "calls"
ISSUES_DIR = DATA_DIR / "issues"


# ------------------------------------------------------------------ calls ---
def load_calls(agent_id: str) -> list[dict]:
    """Every recorded call for an agent — seeded, WebRTC, or answered on the phone.

    Deliberately one list. Issue mining and call-to-test replay shouldn't care
    whether a transcript came from a simulation or from a real patient, and the
    moment they do, the seeded calls stop being a useful rehearsal for the real
    ones.
    """
    if db.enabled():
        import repo

        return repo.load_calls(agent_id)
    path = CALLS_DIR / f"{agent_id}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("calls", [])


def save_calls(agent_id: str, calls: list[dict]) -> None:
    if db.enabled():
        import repo

        repo.save_calls(agent_id, calls)
        return
    CALLS_DIR.mkdir(parents=True, exist_ok=True)
    (CALLS_DIR / f"{agent_id}.json").write_text(
        json.dumps({"calls": calls}, indent=2), encoding="utf-8"
    )


def format_call(call: dict) -> str:
    lines = [
        f"### {call['id']}  ({call.get('outcome', 'unknown')}, {call.get('duration_s', '?')}s)"
    ]
    if call.get("flagged_by"):
        lines.append(f"Flagged by client: {call['flagged_by']}")
    for turn in call.get("turns", []):
        who = turn.get("speaker", "?").upper()
        lines.append(f"{who}: {turn.get('text','')}")
    return "\n".join(lines)


# ----------------------------------------------------------------- issues ---
def load_issues(agent_id: str) -> list[dict]:
    if db.enabled():
        import repo

        return repo.load_issues(agent_id)
    path = ISSUES_DIR / f"{agent_id}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("issues", [])


def save_issues(agent_id: str, issues: list[dict]) -> None:
    if db.enabled():
        import repo

        repo.save_issues(agent_id, issues)
        return
    ISSUES_DIR.mkdir(parents=True, exist_ok=True)
    (ISSUES_DIR / f"{agent_id}.json").write_text(
        json.dumps({"issues": issues}, indent=2), encoding="utf-8"
    )


def set_issue_status(agent_id: str, issue_id: str, status: str) -> list[dict]:
    if db.enabled():
        import repo

        return repo.set_issue_status(agent_id, issue_id, status)
    issues = load_issues(agent_id)
    for issue in issues:
        if issue["id"] == issue_id:
            issue["status"] = status
    save_issues(agent_id, issues)
    return issues


def issue_context(issue: dict) -> str:
    """Render an issue as the Copilot's instruction — the whole point of citing evidence."""
    lines = [
        f"### Production issue: {issue['title']}",
        f"Severity: {issue.get('severity','?')} · Seen in {issue.get('call_count', 0)} of the analysed calls",
        "",
        issue.get("description", ""),
    ]
    if issue.get("affected_nodes"):
        lines.append(f"\nNodes involved: {', '.join(issue['affected_nodes'])}")
    if issue.get("evidence"):
        lines.append("\nEvidence from real calls:")
        for ev in issue["evidence"]:
            lines.append(f"- [{ev.get('call_id','?')}] {ev.get('quote','')}")
    if issue.get("suggested_fix"):
        lines.append(f"\nAnalyst's suggested direction: {issue['suggested_fix']}")
    return "\n".join(lines)


# ------------------------------------------------------------------ mining ---
ISSUE_SCHEMA = {
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short, specific, and about the agent's behaviour. 'Agent has no answer for insurance questions', not 'insurance'.",
                    },
                    "description": {
                        "type": "string",
                        "description": "2-4 sentences: what goes wrong, when it happens, and what it costs the caller or the clinic.",
                    },
                    "category": {
                        "type": "string",
                        "enum": [
                            "missing_capability",
                            "wrong_routing",
                            "data_collection",
                            "dead_end",
                            "tone_or_wording",
                            "compliance_risk",
                        ],
                    },
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                    "call_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Every analysed call that exhibits this issue.",
                    },
                    "affected_nodes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Node names from the graph where this occurs. Empty if it spans the whole call.",
                    },
                    "evidence": {
                        "type": "array",
                        "description": "1-3 short verbatim quotes that demonstrate the issue.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "call_id": {"type": "string"},
                                "quote": {"type": "string"},
                            },
                            "required": ["call_id", "quote"],
                        },
                    },
                    "suggested_fix": {
                        "type": "string",
                        "description": "One sentence on the graph change that would address it.",
                    },
                },
                "required": [
                    "title",
                    "description",
                    "category",
                    "severity",
                    "call_ids",
                    "affected_nodes",
                    "evidence",
                    "suggested_fix",
                ],
            },
        }
    },
    "required": ["issues"],
}

MINER_SYSTEM = """You review recorded calls from a healthcare voice agent and report what is going wrong with the agent.

You are given the agent's node graph and a batch of real call transcripts, some flagged by the client.

Your job is to find the **recurring** failures and cluster them. One issue per distinct root cause, however many calls it appears in. Two calls that both fail because the agent can't answer insurance questions are one issue with two calls, not two issues.

What counts as an issue:
- The caller wanted something the graph has no path for.
- The agent took the wrong branch, or made the caller repeat themselves to get to the right one.
- The agent failed to collect or confirm something it needed.
- The caller got stuck with no acceptable option, or hung up unsatisfied.
- The agent said something clinically or legally unsafe for a clinic to say.

What does not count:
- One-off caller confusion that the agent handled fine.
- Style preferences with no impact on the outcome.
- Speculation about audio quality, latency, or speech recognition — you only have text.

Severity is about consequence, not frequency: a caller who is told the wrong thing about a medication is critical even if it happened once; a slightly clumsy confirmation line is low even if it happens in every call.

Ground everything. Every issue must name the calls it came from and quote them verbatim. Do not report an issue you cannot quote."""


async def mine_issues(
    agent_id: str,
    config: dict,
    calls: Optional[list[dict]] = None,
    llm: Optional[LLMClient] = None,
    persist: bool = True,
) -> list[dict]:
    """Cluster a batch of calls into ranked, evidenced issues."""
    calls = calls if calls is not None else load_calls(agent_id)
    if not calls:
        return []

    llm = llm or copilot_llm()
    transcripts = "\n\n".join(format_call(c) for c in calls)
    prompt = f"""## The agent that handled these calls

```json
{json.dumps(config, indent=2)}
```

## Calls ({len(calls)} total)

{transcripts}

Report the recurring issues, most consequential first."""

    raw = await llm.structured(
        system=MINER_SYSTEM,
        prompt=prompt,
        schema=ISSUE_SCHEMA,
        tool_name="report_issues",
        description="Report clustered issues found across the calls.",
        max_tokens=16000,
    )

    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    existing = {i["title"]: i for i in load_issues(agent_id)}
    issues = []
    for item in raw.get("issues", []):
        prior = existing.get(item["title"], {})
        issues.append(
            {
                "id": prior.get("id") or f"iss_{uuid.uuid4().hex[:8]}",
                "title": item["title"],
                "description": item["description"],
                "category": item["category"],
                "severity": item["severity"],
                "call_ids": item.get("call_ids", []),
                "call_count": len(item.get("call_ids", [])),
                "affected_nodes": item.get("affected_nodes", []),
                "evidence": item.get("evidence", []),
                "suggested_fix": item.get("suggested_fix", ""),
                # Keep a human's earlier decision about this issue.
                "status": prior.get("status", "open"),
                "found_at": prior.get("found_at", time.time()),
            }
        )

    issues.sort(key=lambda i: (rank.get(i["severity"], 9), -i["call_count"]))
    if persist:
        save_issues(agent_id, issues)
    return issues
