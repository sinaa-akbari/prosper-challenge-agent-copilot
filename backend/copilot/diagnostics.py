#
# Failure reports — everything the Copilot needs to work out *why* a test failed.
#
# A failing assertion on its own is a symptom, not a diagnosis. The interesting
# root causes are contradictions between parts of the graph that never appear in
# a transcript, and the classic is a required parameter on an escape hatch:
#
#     the caller refuses their date of birth
#     -> the agent tries to hand them to a human
#     -> `transfer_to_staff` declares date_of_birth as required
#     -> the escape hatch is unreachable for exactly the callers who need it
#
# Nothing in the transcript says that. You can only see it by putting the
# transcript next to the tool schemas that were live at the moment it stalled.
# So that is what this module assembles: the caller, the expectations, the
# annotated transcript, and — the part that makes the difference — the exits and
# their required fields at every node the call actually visited.
#

from typing import Optional

from agent_builder.schema import AgentConfig, Node

MAX_CASES = 4          # a focused report beats an exhaustive one
MAX_TRANSCRIPT_TURNS = 60


def _persona_block(persona: dict) -> str:
    facts = persona.get("facts") or {}
    lines = [
        f"- Who: {persona.get('description', '(unspecified)')}",
        f"- Wants: {persona.get('goal', '(unspecified)')}",
        f"- Behaviour: {persona.get('style', '(unspecified)')}",
    ]
    if facts:
        lines.append(
            "- Will give if asked: "
            + ", ".join(f"{k}={v}" for k, v in facts.items())
        )
        lines.append(
            "- Will NOT give: anything not in that list. If the agent needs "
            "something absent from it, this caller cannot supply it."
        )
    return "\n".join(lines)


def _transcript_block(sim: dict) -> str:
    turns = sim.get("turns") or []
    if not turns:
        return "(the call produced no turns)"

    out = []
    for turn in turns[:MAX_TRANSCRIPT_TURNS]:
        node = turn.get("node", "")
        if turn.get("speaker") == "transition":
            args = turn.get("args") or {}
            detail = f"  {args}" if args else ""
            out.append(
                f"{'':<18} --> {turn.get('function')} -> {turn.get('target')}{detail}"
            )
        else:
            who = turn.get("speaker", "?").upper()
            out.append(f"[{node[:16]:<16}] {who}: {turn.get('text', '')}")
    if len(turns) > MAX_TRANSCRIPT_TURNS:
        out.append(f"... {len(turns) - MAX_TRANSCRIPT_TURNS} more turns omitted")
    return "\n".join(out)


def _signals(config: AgentConfig, sim: dict) -> str:
    """Structural faults visible in the turn sequence but not in the words.

    Two of these account for most failures that survive a wording fix, and both
    are easy to read straight past in a transcript:

      * a node entered and left in the same breath, so the caller never got to
        answer the question it asked;
      * a caller turn the current node had no exit for, which the model covers
        by improvising — and improvising is how it ends up taking an unrelated
        exit a moment later.

    Neither is a phrasing problem, so a Copilot that only sees the transcript
    will keep proposing phrasing fixes. Naming them makes them addressable.
    """
    turns = sim.get("turns") or []
    found = []

    for i, turn in enumerate(turns):
        prev = turns[i - 1] if i else None

        if turn.get("speaker") == "transition":
            if prev is None:
                continue
            if prev.get("speaker") == "transition":
                found.append(
                    f"- **`{prev.get('target')}` was entered and left in the same turn.** "
                    f"`{prev.get('function')}` arrived there and `{turn.get('function')}` "
                    f"left immediately, with no caller turn in between — so anything that "
                    f"node was supposed to ask, the caller never had a chance to answer. "
                    f"Whatever `{prev.get('target')}` exists to do did not happen."
                )
            elif prev.get("speaker") == "agent":
                found.append(
                    f"- **`{turn.get('function')}` was taken off the agent's own turn.** "
                    f"The exit fired straight after the agent spoke, without waiting for a "
                    f"reply, so it reflects what the agent said rather than what the caller "
                    f"asked for."
                )
            continue

        # A caller turn that produced no transition: the node absorbed it.
        if turn.get("speaker") == "caller":
            nxt = turns[i + 1] if i + 1 < len(turns) else None
            if nxt is not None and nxt.get("speaker") == "agent":
                node = config.node(turn.get("node") or "")
                exits = (
                    ", ".join(f"`{e.function}`" for e in config.edges_for(node))
                    if node
                    else "(unknown)"
                )
                found.append(
                    f"- **`{turn.get('node')}` had no exit for what the caller said.** "
                    f'Caller: "{turn.get("text", "")[:220]}" — the agent answered from '
                    f"the node's own instructions and stayed put. Its exits were: {exits}. "
                    f"If that question is one real callers ask, the graph needs a path for "
                    f"it; the model improvising an answer is how calls drift."
                )

    if not found:
        return ""
    # The same structural fault repeats across turns; report each shape once.
    seen, unique = set(), []
    for f in found:
        key = f[:120]
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return (
        "\n### Structural signals (read these before the wording)\n\n"
        "Found by walking the turn sequence, not by reading it. These are graph "
        "faults — changing what a node *says* will not fix them.\n\n"
        + "\n".join(unique)
        + "\n"
    )


def _exits_block(config: AgentConfig, node: Node) -> str:
    edges = config.edges_for(node)
    if not edges:
        return (
            f"  (no exits — this node is {'terminal' if node.end else 'a DEAD END'})"
        )
    own = {e.function for e in node.edges}
    lines = []
    for e in edges:
        scope = "" if e.function in own else "  [global]"
        lines.append(f"  - {e.function} -> {e.target}{scope}")
        lines.append(f"      taken when: {e.description or '(no description!)'}")
        if e.required:
            lines.append(
                f"      REQUIRES: {', '.join(e.required)}"
                "   <- the model cannot take this exit until it has all of these"
            )
        optional = [k for k in (e.properties or {}) if k not in (e.required or [])]
        if optional:
            lines.append(f"      optional: {', '.join(optional)}")
    return "\n".join(lines)


def _reachability_note(config: AgentConfig) -> str:
    """Flag escape hatches gated behind required data — the contradiction class."""
    flagged = []
    for edge in config.global_edges:
        if edge.required:
            flagged.append(
                f"  - global '{edge.function}' requires {edge.required}. A caller who "
                f"cannot or will not provide that has no way to reach '{edge.target}'."
            )
    for node in config.nodes:
        for edge in node.edges:
            target = config.node(edge.target)
            looks_like_escape = target is not None and any(
                w in edge.target.lower() or w in edge.function.lower()
                for w in ("human", "staff", "transfer", "emergency", "operator", "agent_help")
            )
            if looks_like_escape and edge.required:
                flagged.append(
                    f"  - '{edge.function}' on node '{node.name}' looks like an escape "
                    f"hatch but requires {edge.required}."
                )
    if not flagged:
        return ""
    return (
        "\n## Possible contradictions already visible in the graph\n\n"
        "These were found by inspecting the graph, not the transcript. An exit the "
        "caller needs *because* they can't supply something must not require that "
        "thing.\n\n" + "\n".join(flagged) + "\n"
    )


def failure_report(
    config: AgentConfig,
    run: dict,
    cases_by_id: dict,
    max_cases: int = MAX_CASES,
) -> str:
    """Render failing cases from a run into a diagnostic brief for the Copilot."""
    failures = [r for r in run.get("results", []) if not r.get("passed")]
    if not failures:
        return ""

    parts = [
        f"# Test run: {run.get('passed', 0)}/{run.get('total', 0)} passing, "
        f"{len(failures)} failing\n"
    ]

    for result in failures[:max_cases]:
        case = cases_by_id.get(result.get("case_id")) or {}
        sim = result.get("simulation") or {}
        verdict = result.get("verdict") or {}

        parts.append(
            f"\n---\n\n## FAILING TEST: {result.get('name')}\n"
            f"(case_id `{result.get('case_id')}` — use this if you retire the test)\n"
        )
        if case.get("persona"):
            parts.append("### The caller\n" + _persona_block(case["persona"]) + "\n")

        parts.append("### What was expected")
        for a in verdict.get("results", []):
            mark = "PASSED" if a.get("passed") else "FAILED"
            parts.append(f"- [{mark}] {a.get('assertion')}")
            if not a.get("passed"):
                parts.append(f"    judge: {a.get('reason')}")
                if a.get("evidence"):
                    parts.append(f"    evidence: {a.get('evidence')}")
        if verdict.get("error"):
            parts.append(f"- harness error: {verdict['error']}")

        path = sim.get("path") or []
        end = sim.get("end_reason", "?")
        end_note = {
            "hangup": "the simulated caller gave up and hung up",
            "max_turns": "the conversation hit the turn limit without finishing — "
                         "usually a loop",
            "terminal": "the call reached a terminal node",
            "error": "the harness errored",
        }.get(end, end)
        parts.append(
            f"\n### What actually happened\n"
            f"- Path: {' -> '.join(path) if path else '(never left the entry node)'}\n"
            f"- Ended: {end} — {end_note}\n"
            f"- Collected: {sim.get('collected') or '{}'}\n"
        )
        parts.append("Transcript (node in brackets, `-->` is a state transition):\n")
        parts.append("```\n" + _transcript_block(sim) + "\n```\n")

        signal = _signals(config, sim)
        if signal:
            parts.append(signal)

        # The part a transcript can't show: what the model could actually do.
        visited = list(dict.fromkeys(path)) or [config.initial_node]
        parts.append("### Exits available at each node this call visited\n")
        for name in visited:
            node = config.node(name)
            if node is None:
                continue
            parts.append(f"Node `{name}`:")
            parts.append(_exits_block(config, node))
        parts.append("")

    if len(failures) > max_cases:
        parts.append(f"\n({len(failures) - max_cases} further failures not shown.)\n")

    parts.append(_reachability_note(config))
    return "\n".join(parts)


def build_context(
    config: AgentConfig,
    run: Optional[dict],
    cases: Optional[list] = None,
) -> str:
    """Convenience wrapper: turn a stored run + its cases into Copilot context."""
    if not run:
        return ""
    cases_by_id = {c.id: c.to_dict() for c in (cases or [])}
    return failure_report(config, run, cases_by_id)


def structural_signals(config: AgentConfig, run: Optional[dict]) -> dict:
    """case_id -> the structural fault on that call, as one plain sentence.

    The same detection the report renders, returned as data so the loop can
    check a diagnosis against it before spending a verification run on one that
    contradicts the evidence.
    """
    if not run:
        return {}
    out = {}
    for result in run.get("results", []):
        if result.get("passed"):
            continue
        block = _signals(config, result.get("simulation") or {})
        if not block:
            continue
        # The first bullet is the primary fault; strip the markdown emphasis so
        # it reads as a sentence when quoted back.
        first = next(
            (ln for ln in block.splitlines() if ln.startswith("- ")), ""
        )
        cleaned = first[2:].replace("**", "").strip()
        if cleaned:
            out[result.get("case_id")] = cleaned
    return out
