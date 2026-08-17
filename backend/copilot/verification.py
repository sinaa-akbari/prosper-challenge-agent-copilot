#
# Running the proposal before a human ever sees it.
#
# The Copilot's original loop asked one question — "does this apply, and does it
# lint?" That is a compile check, and it lets through a diff that assembles
# perfectly and fixes nothing. Watching it work, the failure was always the same
# shape: it proposes a wording change, the wording change does not move the
# behaviour, and the human gate catches a dud several minutes later.
#
# So the loop now asks the question that matters: did the call actually change?
# The candidate graph runs the failing cases, and if they still fail, the new
# transcripts go back to the model as evidence. That is the same feedback the
# linter already provides, aimed at behaviour instead of syntax.
#
# Two things this deliberately does *not* do:
#
#   * It doesn't run the whole suite. A full run costs a case for every second
#     the engineer waits, and most cases have nothing to do with the change.
#   * It doesn't treat "all green" as success on its own. A model that can edit
#     the tests can always reach green by editing the tests, so retirements are
#     excluded from the score and surfaced to the human instead of counted.
#

from dataclasses import dataclass, field
from typing import Optional

from agent_builder.patch import affected_nodes
from agent_builder.schema import AgentConfig

from .diagnostics import failure_report

# The neighbours are here to catch collateral damage, not to re-run the suite.
MAX_NEIGHBOURS = 3


@dataclass
class VerifyOutcome:
    run: dict
    fixed: list[str] = field(default_factory=list)
    still_failing: list[str] = field(default_factory=list)
    broke: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    report: str = ""

    @property
    def clean(self) -> bool:
        """Nothing failing *and* something actually fixed.

        The second half is load-bearing. Without it, a round that retires every
        failing case leaves nothing to fail, reports clean, and short-circuits
        the loop before the score is ever consulted — deleting the tests becomes
        the fastest way to finish. Absence of red is not evidence of a fix.
        """
        return bool(self.fixed) and not self.still_failing and not self.broke

    def summary(self) -> str:
        bits = []
        if self.fixed:
            bits.append(f"fixed {len(self.fixed)}")
        if self.still_failing:
            bits.append(f"{len(self.still_failing)} still failing")
        if self.broke:
            bits.append(f"broke {len(self.broke)}")
        if self.retired:
            bits.append(f"{len(self.retired)} retired (not scored)")
        return ", ".join(bits) or "nothing to check"

    def to_dict(self) -> dict:
        return {
            "fixed": self.fixed,
            "still_failing": self.still_failing,
            "broke": self.broke,
            "retired": self.retired,
            "passed": self.run.get("passed", 0),
            "total": self.run.get("total", 0),
        }


def blast_radius(ops: list[dict], run: Optional[dict]) -> list[str]:
    """Passing cases whose path crosses a node this proposal touches.

    A change is only safe if the paths through the node it edits still work, and
    the Copilot has no way to know which those are — it edited `greeting` to fix
    a cancellation and took the new-patient booking flow down with it, which no
    amount of reasoning about the diff would have caught. The stored run already
    records the path every case took, so the answer is a set intersection.
    """
    if not run or not ops:
        return []
    touched = set(affected_nodes(ops))
    if not touched:
        return []

    hits = []
    for result in run.get("results", []):
        if not result.get("passed"):
            continue  # already-failing cases are selected as the focus set
        path = set((result.get("simulation") or {}).get("path") or [])
        overlap = path & touched
        if overlap:
            # Most overlap wins: the case that spends the most of its life in the
            # nodes being edited is the one most likely to notice.
            hits.append((len(overlap), result.get("case_id"), result.get("name")))

    hits.sort(reverse=True)
    return [case_id for _, case_id, _ in hits[:MAX_NEIGHBOURS] if case_id]


def build_outcome(
    config: AgentConfig,
    run: dict,
    cases_by_id: dict,
    focus_ids: set,
    neighbour_ids: set,
    retired_ids: set,
) -> VerifyOutcome:
    """Score a candidate run against what it was supposed to achieve."""
    outcome = VerifyOutcome(run=run)

    for result in run.get("results", []):
        case_id, name = result.get("case_id"), result.get("name", "?")
        if result.get("passed"):
            if case_id in focus_ids:
                outcome.fixed.append(name)
            continue
        if case_id in neighbour_ids:
            outcome.broke.append(name)
        else:
            outcome.still_failing.append(name)

    outcome.retired = sorted(
        cases_by_id.get(cid, {}).get("name", cid) for cid in retired_ids
    )

    if not outcome.clean:
        # The same brief the first attempt was given, regenerated against what
        # just happened — so the model is corrected by evidence rather than told
        # off. The structural signals recompute too, which is the point: if a
        # node is still being passed through, that shows up again.
        failing = {
            r.get("case_id"): r
            for r in run.get("results", [])
            if not r.get("passed")
        }
        outcome.report = failure_report(
            config,
            {**run, "results": list(failing.values())},
            cases_by_id,
            max_cases=3,
        )
    return outcome


def feedback(outcome: VerifyOutcome, attempt: int, attempts_left: int) -> str:
    """What the model is told after a candidate failed to deliver."""
    lines = [
        f"You proposed a fix and it was run before being shown to anyone. "
        f"Attempt {attempt}: {outcome.summary()}.",
        "",
    ]
    if not outcome.run.get("total"):
        # Only reachable by retiring everything that was going to be checked.
        lines.append(
            "**Nothing was left to run.** You retired every case this change would "
            "have been measured against, so there is no evidence your graph edits do "
            "anything at all. That is not a passing result. Either keep the tests and "
            "fix the cause, or make no graph change and retire only what you can "
            "justify — but do not do both at once."
        )
    if outcome.fixed:
        lines.append(
            f"Now passing: {', '.join(outcome.fixed)}. Keep whatever produced that."
        )
    if outcome.broke:
        lines.append(
            f"**You broke {', '.join(outcome.broke)}** — these passed before your "
            "change. They run through a node you edited. Whatever you did there has "
            "to stop breaking them, and reverting that specific edit is a legitimate "
            "answer."
        )
    if outcome.still_failing:
        lines.append(
            f"Still failing: {', '.join(outcome.still_failing)}. Your diagnosis of "
            "these was wrong, or the fix didn't address it. Do not repeat the same "
            "kind of change and hope — if you rewrote instructions and the behaviour "
            "did not move, the cause is structural."
        )
    if outcome.report:
        lines += ["", "Here is what happened on the new run:", "", outcome.report]

    lines += [
        "",
        f"You have {attempts_left} attempt(s) left. Emit a complete, corrected list "
        "of operations — it replaces your previous list rather than adding to it. "
        "If you now believe the graph is right and the test is wrong, say so and "
        "retire it with your evidence; do not keep editing the graph to chase an "
        "assertion you think is incorrect.",
    ]
    return "\n".join(lines)
