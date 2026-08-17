"""Dev-only checks for the Copilot's verification loop, scoring and memory.

The loop's behaviour under failure is the part that matters and the part that's
expensive to reach through a live call, so the model is stubbed here and only the
machinery is exercised: does it retry when a proposal doesn't work, does it keep
the best attempt rather than the last, does it refuse to reward deleting tests,
and does it stop.
"""

import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import db  # noqa: E402
from agent_builder import store  # noqa: E402
import tenancy  # noqa: E402

tenancy.use_default_workspace()
from agent_builder.schema import AgentConfig  # noqa: E402
from copilot import blast_radius, decisions_context, memory  # noqa: E402
from copilot.agent import _structural_pushback, propose  # noqa: E402
from copilot.verification import VerifyOutcome, build_outcome  # noqa: E402

PROBLEMS = []


def check(ok, msg):
    print(f"  {'ok  ' if ok else 'FAIL'}  {msg}")
    if not ok:
        PROBLEMS.append(msg)


class StubLLM:
    """Returns a scripted proposal per call, so the loop can be driven."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    async def structured(self, system, prompt, **kw):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else self.replies_exhausted()

    def replies_exhausted(self):
        raise AssertionError("the loop asked for more attempts than were scripted")


def edit(text):
    return {
        "reply": text,
        "ops": [{"op": "update_node", "name": "greeting", "patch": {"task_messages": [text]}}],
        "findings": [],
    }


def outcome(fixed=(), still=(), broke=(), retired=()):
    return VerifyOutcome(
        run={"passed": len(fixed), "total": len(fixed) + len(still) + len(broke)},
        fixed=list(fixed),
        still_failing=list(still),
        broke=list(broke),
        retired=list(retired),
    )


async def main():
    config = store.get_config("northside-scheduling")

    # --- retries while the proposal doesn't work, then stops -----------------
    print("\nloop: retries on a proposal that changes nothing")
    llm = StubLLM([edit("first"), edit("second"), edit("third"), edit("fourth")])
    calls = []

    async def never_works(candidate, ops, retire_ids, extra):
        calls.append(ops)
        return outcome(still=["Angela"])

    result = await propose(config, "fix it", llm=llm, verify=never_works)
    check(len(calls) == 3, f"stops after 3 rounds rather than looping (ran {len(calls)})")
    check(result["error"] == "", "still returns a reviewable diff after giving up")
    check(
        "still failing" in llm.prompts[-1].lower(),
        "the model is told what happened, not just that it failed",
    )

    # --- keeps the best attempt, not the last --------------------------------
    print("\nloop: keeps the best attempt")
    llm = StubLLM([edit("good"), edit("worse"), edit("worst")])
    seq = iter(
        [
            outcome(fixed=["Angela"], still=["Derek"]),   # best
            outcome(still=["Angela", "Derek"]),
            outcome(still=["Angela", "Derek"], broke=["Maria"]),
        ]
    )

    async def declining(candidate, ops, retire_ids, extra):
        return next(seq)

    result = await propose(config, "fix it", llm=llm, verify=declining)
    check(
        result["reply"] == "good",
        f"returns the round that did best, not the final one (got '{result['reply']}')",
    )
    check(result["verification"]["fixed"] == ["Angela"], "reports the best round's outcome")

    # --- a clean run returns immediately -------------------------------------
    print("\nloop: stops as soon as it works")
    llm = StubLLM([edit("works")])
    ran = []

    async def works(candidate, ops, retire_ids, extra):
        ran.append(1)
        return outcome(fixed=["Angela", "Derek"])

    result = await propose(config, "fix it", llm=llm, verify=works)
    check(len(ran) == 1, "doesn't keep going once the calls pass")
    check(result["verification"]["fixed"] == ["Angela", "Derek"], "reports what it fixed")

    # --- breaking a passing case scores worse than fixing nothing ------------
    print("\nscoring: collateral damage is disqualifying")
    llm = StubLLM([edit("breaks-things"), edit("safe"), edit("safe2")])
    seq2 = iter(
        [
            outcome(fixed=["Angela", "Derek"], broke=["Maria", "Sam"]),  # 2 - 4 = -2
            outcome(fixed=["Angela"], still=["Derek"]),                  # 1 - 0 =  1
            outcome(still=["Angela", "Derek"]),                          # 0
        ]
    )

    async def mixed(candidate, ops, retire_ids, extra):
        return next(seq2)

    result = await propose(config, "fix it", llm=llm, verify=mixed)
    check(
        result["reply"] == "safe",
        f"prefers one safe fix over two fixes that break two others (got '{result['reply']}')",
    )

    # --- retiring tests earns nothing ----------------------------------------
    print("\nscoring: deleting tests is not a way to win")
    llm = StubLLM([edit("real-fix"), edit("delete-them"), edit("delete-more")])
    seq3 = iter(
        [
            outcome(fixed=["Angela"], still=["Derek"]),
            outcome(retired=["Angela", "Derek"]),
            outcome(retired=["Angela", "Derek"]),
        ]
    )

    async def gaming(candidate, ops, retire_ids, extra):
        return next(seq3)

    result = await propose(config, "fix it", llm=llm, verify=gaming)
    check(
        result["reply"] == "real-fix",
        f"a round that retires everything doesn't outrank a real fix (got '{result['reply']}')",
    )

    # --- verification that explodes doesn't lose the diff --------------------
    print("\nloop: a broken verifier doesn't swallow the proposal")
    llm = StubLLM([edit("fine")])

    async def explodes(candidate, ops, retire_ids, extra):
        raise RuntimeError("simulator unavailable")

    result = await propose(config, "fix it", llm=llm, verify=explodes)
    check(len(result["ops"]) == 1, "the diff survives a verifier failure")
    check(
        "simulator unavailable" in (result["verification"] or {}).get("error", ""),
        "and the failure is reported rather than hidden",
    )

    # --- coherence: don't leave the graph worse than you found it -------------
    print("\ncoherence: a change that strands a node is sent back")

    def orphaning_edit():
        """Delete the only edge into verify_identity, leaving it unreachable."""
        return {
            "reply": "removed identity",
            "ops": [{"op": "delete_edge", "from": "greeting", "function": "existing_appointment"}],
            "findings": [],
        }

    def clean_edit():
        return {"reply": "tightened wording", "ops": [
            {"op": "update_node", "name": "greeting", "patch": {"task_messages": [
                {"role": "developer", "content": "Greet the caller."}]}}], "findings": []}

    llm = StubLLM([orphaning_edit(), clean_edit()])
    result = await propose(config, "get rid of the identity stuff", llm=llm)
    check(
        len(llm.prompts) == 2,
        f"a newly-stranded node triggers a repair round (asked {len(llm.prompts)}x)",
    )
    check(
        "unreachable" in llm.prompts[-1].lower(),
        "and the model is told exactly what it stranded",
    )
    check(
        "first and only answer" in llm.prompts[-1],
        "and told the engineer never saw the earlier attempt",
    )
    check(result["reply"] == "tightened wording", "the corrected attempt is what ships")

    print("\ncoherence: pre-existing problems are not blamed on the change")
    # A graph that already has an orphan must still be editable.
    broken = json.loads(json.dumps(config))
    broken["nodes"].append(
        {"name": "stranded", "task_messages": [{"role": "developer", "content": "x"}], "edges": []}
    )
    llm = StubLLM([clean_edit()])
    result = await propose(broken, "tighten the greeting", llm=llm)
    check(len(llm.prompts) == 1, "an inherited warning doesn't trigger a repair")
    check(len(result["ops"]) == 1, "and the edit goes through")
    check(
        any("stranded" in i["message"] for i in result["lint"]),
        "the warning is still reported",
    )
    check(
        not any("stranded" in i["message"] for i in result["new_lint"]),
        "but not as one this change introduced",
    )

    # --- structural pushback --------------------------------------------------
    print("\npushback: a wording diagnosis against a structural signal")
    signals = {"tc_1": "`cancellation_done` was entered and left in the same turn."}
    wording = [{"case_id": "tc_1", "root_cause": "edge_description_mismatch"}]
    structural = [{"case_id": "tc_1", "root_cause": "node_passed_through"}]
    check(bool(_structural_pushback(wording, signals)), "rejects a wording cause")
    check(not _structural_pushback(structural, signals), "accepts a structural cause")
    check(not _structural_pushback(wording, {}), "stays quiet when there is no signal")

    llm = StubLLM(
        [
            {"reply": "wording", "ops": [], "findings": wording},
            {"reply": "structural", "ops": [], "findings": structural},
        ]
    )
    result = await propose(config, "fix it", llm=llm, signals=signals)
    check(result["reply"] == "structural", "the loop makes it re-diagnose once")
    check(len(llm.prompts) == 2, "and only once, so it can't argue forever")

    # --- blast radius ---------------------------------------------------------
    print("\nblast radius")
    run = {
        "results": [
            {"case_id": "a", "passed": True, "simulation": {"path": ["greeting", "confirm"]}},
            {"case_id": "b", "passed": True, "simulation": {"path": ["other"]}},
            {"case_id": "c", "passed": False, "simulation": {"path": ["greeting"]}},
        ]
    }
    ops = [{"op": "update_node", "name": "greeting", "patch": {}}]
    hits = blast_radius(ops, run)
    check("a" in hits, "picks up a passing case that routes through the edited node")
    check("b" not in hits, "ignores cases that don't touch it")
    check("c" not in hits, "ignores already-failing cases, which are the focus set")
    check(blast_radius([], run) == [], "no ops, no neighbours")

    # --- outcome classification ----------------------------------------------
    print("\noutcome classification")
    cand = {
        "results": [
            {"case_id": "f1", "name": "Angela", "passed": True},
            {"case_id": "f2", "name": "Derek", "passed": False},
            {"case_id": "n1", "name": "Maria", "passed": False},
        ],
        "passed": 1,
        "total": 3,
    }
    o = build_outcome(
        AgentConfig.from_dict(config), cand, {}, {"f1", "f2"}, {"n1"}, set()
    )
    check(o.fixed == ["Angela"], "a focus case that now passes is 'fixed'")
    check(o.still_failing == ["Derek"], "a focus case that still fails is 'still failing'")
    check(o.broke == ["Maria"], "a neighbour that now fails is 'broke'")
    check(not o.clean, "and that is not clean")

    # --- decision memory ------------------------------------------------------
    print("\ndecision memory")
    agent_id = "_memtest"
    path = memory.DATA_DIR / "agents" / agent_id
    shutil.rmtree(path, ignore_errors=True)
    # Decisions hang off an agent by foreign key once Postgres is the store, so
    # the scratch agent has to actually exist.
    store.delete(agent_id)
    store.create({**config, "name": "Memory test"}, agent_id=agent_id)
    check(decisions_context(agent_id) == "", "no decisions, no context")

    memory.record_acceptance(
        agent_id,
        label="Copilot: production fix",
        reply="Escape hatches must require nothing, or the caller who needs one can't reach it.",
        ops=[{"op": "update_edge"}],
        retired=[{"name": "DOB test", "reason": "Its two assertions contradict each other."}],
        added=[{"name": "New coverage"}],
    )
    ctx = decisions_context(agent_id)
    check("Escape hatches must require nothing" in ctx, "keeps the reasoning, not just the label")
    check("Do not propose them again" in ctx, "warns against re-adding a retired test")
    check("DOB test" in ctx and "contradict" in ctx, "carries the retirement and its reason")
    check("New coverage" in ctx, "records added coverage too")

    memory.record_acceptance(agent_id, label="second", reply="another reason", ops=[{"op": "x"}])
    check(len(memory.load_decisions(agent_id)) == 4, "appends rather than overwrites")

    if not db.enabled():
        # Only the file-backed log can be corrupt in this way.
        (path / "decisions.json").write_text("{not json", encoding="utf-8")
        check(decisions_context(agent_id) == "", "a corrupt log degrades to no memory, not a crash")
    shutil.rmtree(path, ignore_errors=True)
    store.delete(agent_id)

    print()
    if PROBLEMS:
        print("PROBLEMS:")
        for p in PROBLEMS:
            print("  -", p)
        sys.exit(1)
    print("Copilot loop checks passed.")


asyncio.run(main())
