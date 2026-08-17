#
# The Copilot — natural language in, reviewable graph diff out.
#
# The important behaviour here isn't the model call, it's the loop around it.
# Before a proposal is ever shown to a human, the Copilot applies its own patch,
# runs the graph linter over the result, and — if it produced something broken —
# gets told exactly what broke and tries again. It is the same compile-fix loop
# a coding agent runs, and it means the diff a human reviews is one that at
# least assembles.
#

import json
from typing import Optional

from agent_builder import lint, summarize
from agent_builder.patch import PatchError, affected_nodes, apply_ops
from agent_builder.schema import AgentConfig
from llm import LLMClient, copilot_llm

from .prompts import system_prompt
from .verification import feedback as verification_feedback

MAX_REPAIR_ATTEMPTS = 2

# Each round costs a full set of simulated calls, and a model that hasn't found
# the cause in three passes is guessing rather than converging. The cap is also
# what stops the loop turning into a search for whatever makes the tests green.
MAX_VERIFY_ROUNDS = 3

OP_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "op": {
            "type": "string",
            "enum": [
                "set_meta",
                "set_initial_node",
                "add_node",
                "update_node",
                "rename_node",
                "delete_node",
                "add_edge",
                "update_edge",
                "delete_edge",
                "add_global_edge",
                "delete_global_edge",
            ],
        },
        "name": {"type": "string", "description": "Node name, for node-scoped ops."},
        "new_name": {"type": "string", "description": "rename_node only."},
        "from": {"type": "string", "description": "Node the edge belongs to, for edge ops."},
        "function": {"type": "string", "description": "Edge function name, for update/delete edge."},
        "node": {"type": "object", "description": "Full node object, for add_node."},
        "edge": {"type": "object", "description": "Edge object (full for add, partial for update)."},
        "patch": {"type": "object", "description": "Field changes, for update_node."},
        "persona": {"type": "string"},
        "voice_id": {"type": "string"},
        "model": {"type": "string"},
    },
    "required": ["op"],
}

TEST_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "persona": {
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "goal": {"type": "string"},
                "facts": {"type": "object", "additionalProperties": {"type": "string"}},
                "style": {"type": "string"},
            },
            "required": ["description", "goal", "facts", "style"],
        },
        "assertions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "persona", "assertions"],
}

# The classes a failure can belong to. A closed list rather than free text
# because the point is to force a commitment: "the wording is unclear" is a
# diagnosis you can reach without reading anything, and it was the model's
# reliable retreat whenever the real cause was structural.
ROOT_CAUSE_CLASSES = [
    "required_field_blocks_exit",
    "node_passed_through",
    "missing_path",
    "edge_description_mismatch",
    "node_overloaded",
    "conflicting_instructions",
    "broken_test",
]

# Classes that a change to what a node *says* could plausibly fix. If the report
# carried a structural signal for the same case, one of these is the wrong answer
# — nothing the node says is reaching the caller.
WORDING_CLASSES = {"edge_description_mismatch", "conflicting_instructions"}

FINDING_SCHEMA = {
    "type": "object",
    "properties": {
        "case_id": {"type": "string", "description": "The failing test this explains."},
        "case_name": {"type": "string"},
        "root_cause": {
            "type": "string",
            "enum": ROOT_CAUSE_CLASSES,
            "description": "The class of fault. Pick the cause, not the symptom.",
        },
        "evidence": {
            "type": "string",
            "description": (
                "Quote the transcript turn, or name the structural signal, that "
                "proves this is the cause. An assertion's failure text is a "
                "symptom and is not evidence. If you cannot cite something "
                "specific, you have not found the cause yet."
            ),
        },
        "fix": {
            "type": "string",
            "description": "Which of your operations addresses this, or why none does.",
        },
    },
    "required": ["case_id", "root_cause", "evidence", "fix"],
}

RETIRE_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "case_id": {
            "type": "string",
            "description": "The case_id of the existing test, exactly as given in the failure report.",
        },
        "name": {"type": "string", "description": "Its name, so the engineer recognises it."},
        "reason": {
            "type": "string",
            "description": "Why the test is wrong, not why it failed. One or two sentences.",
        },
    },
    "required": ["case_id", "name", "reason"],
}

RESPONSE_SCHEMA = {
    "properties": {
        "reply": {
            "type": "string",
            "description": "What the engineer reads. Short and direct. Empty ops means this is just an answer.",
        },
        "ops": {
            "type": "array",
            "items": OP_ITEM_SCHEMA,
            "description": "Operations to apply, in order. Empty if no change is needed.",
        },
        "tests": {
            "type": "array",
            "items": TEST_ITEM_SCHEMA,
            "description": (
                "Regression tests that prove this change works. Include these ONLY when "
                "fixing a reported production issue or a failing test, or when asked."
            ),
        },
        "findings": {
            "type": "array",
            "items": FINDING_SCHEMA,
            "description": (
                "One per failing test in the report. Required whenever a failure "
                "report is attached — diagnose before you patch."
            ),
        },
        "retire_tests": {
            "type": "array",
            "items": RETIRE_ITEM_SCHEMA,
            "description": (
                "Existing tests you have judged to be wrong — unsatisfiable, "
                "self-contradictory, or asserting something no correct agent could do. "
                "Only for tests that are wrong, never for tests that are merely failing."
            ),
        },
    },
    "required": ["reply", "ops"],
}


def _graph_context(config: dict) -> str:
    """Current graph plus anything the linter already dislikes about it."""
    try:
        issues = lint(AgentConfig.from_dict(config))
    except Exception:
        issues = []
    block = f"```json\n{json.dumps(config, indent=2)}\n```"
    if issues:
        listed = "\n".join(f"- [{i.severity}] {i.message}" for i in issues)
        block += f"\n\nThe linter currently reports:\n{listed}"
    return block


def _structural_pushback(findings: list, signals: dict) -> str:
    """Reject a wording diagnosis when the report said the node was never heard.

    The one correction worth making automatically. If a node is entered and left
    in the same turn, nothing it says reaches the caller, so "the description is
    unclear" cannot be the cause — and that was the answer the model kept
    reaching for. Everything else is left to the verify loop, which corrects with
    evidence instead of argument.
    """
    if not signals:
        return ""
    complaints = []
    for f in findings:
        cause = f.get("root_cause")
        signal = signals.get(f.get("case_id"))
        if signal and cause in WORDING_CLASSES:
            complaints.append(
                f"- For '{f.get('case_name') or f.get('case_id')}' you diagnosed "
                f"`{cause}`, but the report flagged a structural signal on that call: "
                f"{signal} A change to what a node says cannot fix that, because "
                f"nothing it says is reaching the caller."
            )
    if not complaints:
        return ""
    return (
        "Your diagnosis contradicts the evidence you were given:\n\n"
        + "\n".join(complaints)
        + "\n\nRe-diagnose those cases against the structural signal and emit a "
        "corrected list of operations."
    )


def _repair_feedback(errors: list, new_warnings: list) -> str:
    """What the model is told when its change didn't leave a coherent graph."""
    nl = "\n"
    parts = []
    if errors:
        parts.append(
            "Your operations applied, but the resulting graph is invalid:" + nl
            + nl.join(f"- {i.message}" for i in errors)
        )
    if new_warnings:
        parts.append(
            "Your change introduced problems that were not there before:" + nl
            + nl.join(f"- {i.message}" for i in new_warnings)
            + nl + nl
            + "These are yours to resolve, not the engineer's. A node nobody "
            "can reach is not a smaller version of the feature they asked for — "
            "either wire it back in, or delete it and say so. Leaving the graph "
            "less coherent than you found it is not an acceptable outcome of any "
            "request, however vague."
        )
    parts.append(
        "Emit a corrected, complete list of operations — it replaces your "
        "previous list rather than adding to it." + nl + nl
        + "Write `reply` for the engineer, describing the final change only. They "
        "have not seen this attempt or any earlier one, so it must read as your "
        "first and only answer." + nl
        + "  Wrong: \"I tightened the flow and fixed the invalid edge from my "
        "last attempt.\"" + nl
        + "  Right: \"I tightened the flow so each call wraps up faster.\"" + nl
        + "The correction is invisible to them. Mentioning it reads as though "
        "you have confused them with someone else."
    )
    return (nl + nl).join(parts)


def _result(
    config: dict,
    new_config: dict,
    reply: str,
    ops: list,
    issues: list,
    raw: dict,
    verification: Optional[dict] = None,
    introduced: Optional[list] = None,
) -> dict:
    return {
        "reply": reply,
        "ops": ops,
        "diff": [{"op": o.get("op"), "summary": summarize(o), "detail": o} for o in ops],
        "affected": affected_nodes(ops),
        "config": new_config if ops else config,
        "lint": [i.to_dict() for i in issues],   # warnings survive; errors can't
        # Which of those the change caused. A pre-existing warning is noise on a
        # diff; one this edit created is a reason to look again.
        "new_lint": [i.to_dict() for i in (introduced or [])],
        "tests": raw.get("tests") or [],
        "retire_tests": raw.get("retire_tests") or [],
        "findings": raw.get("findings") or [],
        "verification": verification,
        "error": "",
    }


async def propose(
    config: dict,
    message: str,
    history: Optional[list] = None,
    context: str = "",
    llm: Optional[LLMClient] = None,
    verify=None,
    signals: Optional[dict] = None,
    on_status=None,
) -> dict:
    """Turn a request into a validated, reviewable patch.

    `context` carries attached evidence — a mined production issue with call
    quotes, or a failing test run. It's what makes "fix this" a complete
    instruction.

    `verify(candidate, ops, retire_ids, extra_cases) -> VerifyOutcome` runs the
    proposal before anyone sees it. Supplying it turns the loop from a compile
    check into a test-driven one: a proposal that lints but doesn't change the
    call gets sent back with the transcript that proves it. `signals` maps
    case_id to the structural fault found on that call, used to reject a wording
    diagnosis before spending a run on it.
    """
    llm = llm or copilot_llm()
    history = history or []
    signals = signals or {}
    say = on_status or (lambda _msg: None)

    user_block = f"""## Current agent

{_graph_context(config)}
"""
    if context:
        user_block += f"\n## Attached context\n\n{context}\n"
    user_block += f"\n## Request\n\n{message}"

    # Whatever is already wrong with this graph isn't the Copilot's doing, and
    # blaming it for pre-existing warnings would make every edit unacceptable.
    # What it *must* not do is add new ones.
    try:
        pre_existing = {i.message for i in lint(AgentConfig.from_dict(config))}
    except Exception:
        pre_existing = set()

    convo = list(history) + [{"role": "user", "content": user_block}]
    repairs = 0        # operations that don't apply, or graphs that don't lint
    rounds = 0         # full diagnose-fix-verify rounds
    nudged = False     # the structural pushback fires at most once
    feedback: str = ""
    best: Optional[tuple] = None   # (score, result) — kept if later rounds do worse

    while True:
        prompt = convo[-1]["content"] + (
            f"\n\n## Your previous attempt failed\n\n{feedback}" if feedback else ""
        )
        try:
            raw = await llm.structured(
                system=system_prompt(),
                prompt=prompt,
                schema=RESPONSE_SCHEMA,
                tool_name="propose_changes",
                description="Reply to the engineer and propose graph operations.",
                max_tokens=16000,
            )
        except Exception as exc:
            if best:
                return best[1]
            return _failure(f"The Copilot call failed: {type(exc).__name__}: {exc}")

        ops = raw.get("ops") or []
        reply = raw.get("reply", "")
        findings = raw.get("findings") or []

        # A diagnosis that contradicts the evidence is worth one correction
        # before it costs a verification run.
        if findings and not nudged:
            pushback = _structural_pushback(findings, signals)
            if pushback:
                nudged = True
                say("Re-checking a diagnosis against the structural evidence…")
                feedback = pushback
                continue

        # No graph change doesn't mean no proposal: "this test is wrong" is a
        # finding the engineer still has to accept or reject.
        if not ops:
            return _result(config, config, reply, [], [], raw)

        # --- apply and check, exactly as the server would ------------------
        try:
            new_config = apply_ops(config, ops)
        except PatchError as exc:
            repairs += 1
            if repairs > MAX_REPAIR_ATTEMPTS:
                if best:
                    return best[1]
                return _failure(
                    f"The Copilot produced operations that don't apply: {exc}", reply, ops
                )
            feedback = (
                f"Applying your operations raised: {exc}\n\n"
                "Re-read the current graph above and emit a corrected list of operations."
            )
            continue

        issues = lint(AgentConfig.from_dict(new_config))
        errors = [i for i in issues if i.severity == "error"]
        # A warning the change *introduced* is the gap between a diff that
        # compiles and a flow that makes sense. Stranding a node passes the
        # linter and leaves part of the agent unreachable — which is exactly
        # what "the output is broken" looks like from the outside. Pre-existing
        # warnings are not its fault and don't block anything.
        introduced = [i for i in issues if i.message not in pre_existing]
        new_warnings = [i for i in introduced if i.severity == "warning"]

        if errors or new_warnings:
            repairs += 1
            if repairs <= MAX_REPAIR_ATTEMPTS:
                say("Tidying up the flow before showing it…")
                feedback = _repair_feedback(errors, new_warnings)
                continue
            if errors:
                if best:
                    return best[1]
                return _failure(
                    "The Copilot produced an invalid graph: "
                    + "; ".join(i.message for i in errors),
                    reply,
                    ops,
                )
            # Warnings alone don't justify withholding the diff — the engineer
            # can see them and judge. They're marked as newly introduced.
            say("Couldn't fully tidy the flow; showing the change with warnings.")

        if verify is None:
            return _result(config, new_config, reply, ops, issues, raw, introduced=introduced)

        # --- does it actually change the call? -----------------------------
        rounds += 1
        say(f"Running the affected calls against the proposal (round {rounds})…")
        try:
            outcome = await verify(
                new_config,
                ops,
                {t.get("case_id") for t in (raw.get("retire_tests") or [])},
                raw.get("tests") or [],
            )
        except Exception as exc:
            # A verification that fell over is not a reason to withhold a diff
            # that lints — say so and let the human run it themselves.
            return _result(
                config, new_config, reply, ops, issues, raw,
                {"error": f"{type(exc).__name__}: {exc}"},
            )

        result = _result(
            config, new_config, reply, ops, issues, raw,
            outcome.to_dict(), introduced=introduced,
        )
        # Fixes are the goal, collateral damage is disqualifying, and retirements
        # score nothing — otherwise deleting tests is the cheapest way to win.
        score = (len(outcome.fixed) - 2 * len(outcome.broke), -len(outcome.still_failing))
        if best is None or score > best[0]:
            best = (score, result)

        if outcome.clean:
            say(f"Verified: {outcome.summary()}.")
            return result

        rounds_left = MAX_VERIFY_ROUNDS - rounds
        if rounds_left <= 0:
            say(f"Verified: {outcome.summary()}.")
            return best[1]

        say(f"{outcome.summary()} — diagnosing again from the new transcripts…")
        feedback = verification_feedback(outcome, rounds, rounds_left)


def _failure(message: str, reply: str = "", ops: Optional[list] = None) -> dict:
    return {
        "reply": reply or "I couldn't produce a valid change for that.",
        "ops": [],
        "diff": [],
        "affected": {},
        "config": None,
        "lint": [],
        "tests": [],
        "retire_tests": [],
        "findings": [],
        "verification": None,
        "new_lint": [],
        "error": message,
    }
