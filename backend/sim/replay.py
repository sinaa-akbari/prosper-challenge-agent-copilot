#
# Turning a real call into a test case.
#
# The suite's weakest property has been that every persona in it was invented by
# a model. That shows: the generator writes assertions demanding the agent ask
# for information the caller already volunteered, because it is imagining a
# caller rather than reading one. Those cases then flip between pass and fail on
# a knife-edge and teach nobody anything.
#
# A real caller doesn't have that problem. They said what they said, they
# withheld what they withheld, and the call either worked or it didn't. This
# converts one into a case: the persona is reconstructed from the caller's own
# turns, and the assertions describe what *should* have happened, which is not
# the same as what did.
#
# That last point is the whole design. A test written to match a failing call
# would pass immediately and prove nothing.
#

import json
import uuid
from typing import Optional

from llm import LLMClient, copilot_llm

REPLAY_SCHEMA = {
    "properties": {
        "name": {
            "type": "string",
            "description": "Short and specific, describing the caller's situation.",
        },
        "persona": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Who they are and why they called, from their own words.",
                },
                "goal": {"type": "string", "description": "What they were trying to achieve."},
                "facts": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": (
                        "Only what this caller actually supplied or would have. If they "
                        "refused something, leave it out — the refusal is the test."
                    ),
                },
                "style": {
                    "type": "string",
                    "description": (
                        "How they spoke, including anything they refused to do. Mirror the "
                        "real caller: if they were impatient or evasive, say so."
                    ),
                },
            },
            "required": ["description", "goal", "facts", "style"],
        },
        "assertions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "2-4 checkable statements about what the agent SHOULD do.",
        },
        "went_wrong": {
            "type": "string",
            "description": (
                "What actually went wrong on this call, in one sentence. Empty if the "
                "call was handled correctly and this is a keep-it-working test."
            ),
        },
    },
    "required": ["name", "persona", "assertions", "went_wrong"],
}

SYSTEM = """You turn a recorded phone call into a regression test for a voice agent.

You are given a real transcript. Reconstruct the caller as a persona, then write the assertions that describe how the agent *should* have handled them.

Rules that matter:

- The persona is the real caller, not an idealised one. If they refused to give their date of birth, the persona refuses too, and `facts` omits it. If they interrupted, wandered, or asked something off-script, keep that — those are the calls that break agents, and a polite caller tests nothing.
- Put only what the caller actually provided into `facts`. Inventing a value they withheld destroys the test.
- Assertions describe correct behaviour, NOT observed behaviour. If the agent got it wrong, the assertions must fail against this transcript — that is the point of writing them.
- Assert on what the agent ends up with, not how it got there. A caller who volunteers their name in the first breath will never be asked for it, so "the agent asks for their name" fails a correct agent. Write "the agent has their name before confirming".
- Two assertions must be able to hold at once for this caller. The commonest broken test demands the agent collect a field *and* honour an immediate transfer request.
- Assertions must be checkable from a transcript. "The agent is reassuring" is not.
- Transcripts from phone calls contain speech-recognition errors. Read through an obvious mistranscription rather than asserting on it.
"""


def _transcript(call: dict, limit: int = 60) -> str:
    lines = []
    for turn in (call.get("turns") or [])[:limit]:
        who = (turn.get("speaker") or "?").upper()
        lines.append(f"{who}: {turn.get('text', '')}")
    return "\n".join(lines) or "(no turns recorded)"


async def case_from_call(
    call: dict,
    agent_summary: str = "",
    llm: Optional[LLMClient] = None,
) -> dict:
    """Build a TestCase-shaped dict from a recorded call."""
    llm = llm or copilot_llm()

    context = [f"## Call {call.get('id')}"]
    meta = [
        f"outcome: {call.get('outcome', 'unknown')}",
        f"duration: {call.get('duration_s', '?')}s",
        f"source: {call.get('source', 'unknown')}",
    ]
    if call.get("path"):
        meta.append(f"path: {' -> '.join(call['path'])}")
    if call.get("collected"):
        meta.append(f"collected: {json.dumps(call['collected'])}")
    if call.get("flagged_by"):
        meta.append(f"flagged by the client: {call['flagged_by']}")
    context.append(" · ".join(meta))
    context.append("\n### Transcript\n" + _transcript(call))
    if agent_summary:
        context.append("\n### The agent that took this call\n" + agent_summary)

    raw = await llm.structured(
        system=SYSTEM,
        prompt="\n".join(context),
        schema=REPLAY_SCHEMA,
        tool_name="write_case",
        description="Turn this call into a regression test.",
        max_tokens=3000,
    )

    return {
        "id": f"tc_{uuid.uuid4().hex[:8]}",
        "name": raw.get("name") or f"Replay of {call.get('id')}",
        "persona": raw.get("persona") or {},
        "assertions": raw.get("assertions") or [],
        "max_turns": 16,
        "origin": "replay",
        "source_issue": "",
        # The link back to the call. It's what lets you answer "where did this
        # test come from?" a month later, when the answer stops being obvious.
        "source_call": call.get("id", ""),
        "went_wrong": raw.get("went_wrong", ""),
    }
