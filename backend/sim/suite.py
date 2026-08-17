#
# The test suite — storage, generation, and the run loop.
#
# A test case is a simulated caller plus a list of plain-English expectations.
# Three things create them:
#
#   * generate_suite() — bootstraps coverage from the graph itself, so a new
#     agent has a regression net from minute one instead of "some day we should
#     write tests".
#   * The Copilot — when it fixes a production issue, it writes the test that
#     proves the fix, so the issue can never silently come back.
#   * A human, in the UI, when they know a case that matters.
#

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import db
from agent_builder.schema import AgentConfig
from llm import LLMClient, copilot_llm

from .engine import Persona, SimResult, simulate
from .judge import Verdict, judge

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TESTS_DIR = DATA_DIR / "tests"
RUNS_DIR = DATA_DIR / "runs"


@dataclass
class TestCase:
    id: str
    name: str
    persona: dict
    assertions: list
    max_turns: int = 14
    origin: str = "manual"          # manual | generated | regression
    source_issue: str = ""          # issue id, when this is a regression test
    source_call: str = ""           # production call id it was derived from

    @classmethod
    def from_dict(cls, d: dict) -> "TestCase":
        return cls(
            id=d.get("id") or f"tc_{uuid.uuid4().hex[:8]}",
            name=d.get("name", "Untitled case"),
            persona=d.get("persona", {}),
            assertions=d.get("assertions", []),
            max_turns=int(d.get("max_turns", 14)),
            origin=d.get("origin", "manual"),
            source_issue=d.get("source_issue", ""),
            source_call=d.get("source_call", ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- storage ---
def _tests_path(agent_id: str) -> Path:
    return TESTS_DIR / f"{agent_id}.json"


def load_cases(agent_id: str) -> list[TestCase]:
    if db.enabled():
        import repo

        return [TestCase.from_dict(c) for c in repo.load_cases(agent_id)]
    path = _tests_path(agent_id)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [TestCase.from_dict(c) for c in data.get("cases", [])]


def save_cases(agent_id: str, cases: list[TestCase]) -> None:
    if db.enabled():
        import repo

        repo.save_cases(agent_id, [c.to_dict() for c in cases])
        return
    path = _tests_path(agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"cases": [c.to_dict() for c in cases]}, indent=2), encoding="utf-8"
    )


def add_case(agent_id: str, case: TestCase) -> TestCase:
    cases = load_cases(agent_id)
    cases.append(case)
    save_cases(agent_id, cases)
    return case


def delete_case(agent_id: str, case_id: str) -> None:
    save_cases(agent_id, [c for c in load_cases(agent_id) if c.id != case_id])


def load_last_run(agent_id: str) -> Optional[dict]:
    if db.enabled():
        import repo

        return repo.load_last_run(agent_id)
    path = RUNS_DIR / f"{agent_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_run(agent_id: str, run: dict) -> None:
    if db.enabled():
        # Every run is kept, not just the newest. Once fixes land continuously,
        # "is the suite getting better?" is a question about the series.
        import repo

        repo.save_run(agent_id, run)
        return
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / f"{agent_id}.json").write_text(json.dumps(run, indent=2), encoding="utf-8")


# -------------------------------------------------------------- execution ---
async def run_case(config: AgentConfig, case: TestCase) -> dict:
    """Simulate one case and grade it."""
    started = time.time()
    sim: SimResult = await simulate(
        config, Persona.from_dict(case.persona), max_turns=case.max_turns
    )
    if sim.end_reason == "error":
        verdict = Verdict(passed=False, error=sim.error, summary="The simulation failed.")
    else:
        verdict = await judge(
            sim.transcript(),
            case.assertions,
            context=f"The caller's goal was: {case.persona.get('goal', 'unknown')}",
        )
    return {
        "case_id": case.id,
        "name": case.name,
        "origin": case.origin,
        "source_issue": case.source_issue,
        "passed": verdict.passed,
        "verdict": verdict.to_dict(),
        "simulation": sim.to_dict(),
        "duration_s": round(time.time() - started, 1),
    }


async def run_suite(
    agent_id: str,
    config: AgentConfig,
    cases: Optional[list[TestCase]] = None,
    concurrency: int = 5,
    persist: bool = True,
    on_result=None,
) -> dict:
    """Run every case in parallel and return a run record.

    `on_result(result, done, total)` fires as each case lands, so the UI can
    fill in results progressively instead of staring at a spinner.
    """
    cases = cases if cases is not None else load_cases(agent_id)
    started = time.time()

    if not cases:
        run = {
            "agent_id": agent_id,
            "started_at": started,
            "duration_s": 0,
            "total": 0,
            "passed": 0,
            "failed": 0,
            "results": [],
        }
        if persist:
            save_run(agent_id, run)
        return run

    sem = asyncio.Semaphore(concurrency)
    done = 0

    async def guarded(case: TestCase) -> dict:
        nonlocal done
        async with sem:
            try:
                result = await run_case(config, case)
            except Exception as exc:
                result = {
                    "case_id": case.id,
                    "name": case.name,
                    "origin": case.origin,
                    "source_issue": case.source_issue,
                    "passed": False,
                    "verdict": {
                        "passed": False,
                        "results": [],
                        "summary": "",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    "simulation": {},
                    "duration_s": 0,
                }
        done += 1
        if on_result:
            try:
                on_result(result, done, len(cases))
            except Exception:
                pass
        return result

    results = await asyncio.gather(*(guarded(c) for c in cases))
    passed = sum(1 for r in results if r["passed"])
    run = {
        "agent_id": agent_id,
        "started_at": started,
        "duration_s": round(time.time() - started, 1),
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }
    if persist:
        save_run(agent_id, run)
    return run


# ------------------------------------------------------------- generation ---
SUITE_SCHEMA = {
    "properties": {
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Short label, e.g. 'Caller asks about insurance mid-booking'.",
                    },
                    "persona": {
                        "type": "object",
                        "properties": {
                            "description": {
                                "type": "string",
                                "description": "Who they are: name, age, situation. One or two sentences.",
                            },
                            "goal": {"type": "string", "description": "What they want from this call."},
                            "facts": {
                                "type": "object",
                                "description": "Details they'd give if asked (name, DOB, reason, preferred times). Flat string values.",
                                "additionalProperties": {"type": "string"},
                            },
                            "style": {
                                "type": "string",
                                "description": "How they speak, including any behaviour that stresses the agent.",
                            },
                        },
                        "required": ["description", "goal", "facts", "style"],
                    },
                    "assertions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "2-4 checkable statements about what the agent must do.",
                    },
                },
                "required": ["name", "persona", "assertions"],
            },
        }
    },
    "required": ["cases"],
}

GEN_SYSTEM = """You write test cases for voice agents that handle healthcare phone calls.

Each case is a simulated caller (persona) plus assertions about what the agent must do. Good suites cover:
- The happy path for each distinct intent the graph supports.
- Each branch of the graph, including the ones that are easy to forget.
- Realistic friction: callers who change their mind, give information out of order, ask something the script doesn't anticipate, or want an option the agent doesn't offer.

Rules for assertions:
- Each must be objectively checkable from a transcript. "The agent confirms the appointment time back to the caller" is checkable; "the agent is friendly" is not.
- Assert on the agent's behaviour, never the caller's.
- Each must be *satisfiable*. The agent always speaks first with its opening line, and it cannot react to something the caller has not said yet — so never assert that it skips its greeting or responds to information before it is given. A test that no correct agent could pass is a broken test, not a strict one.
- Assert on what the agent *has*, not on how it got it. If the persona volunteers something in their opening turn, a good agent won't ask for it again, so "the agent asks for the reason for visit" fails a correct agent whenever the caller has already said why they're calling. Write "the agent has the reason for visit before offering times" instead. Only assert that something was explicitly asked for when asking is the actual requirement — identity verification, say, where accepting a volunteered name would be the bug. Then say so, so the grader knows it's deliberate.
- Assertions must not contradict each other. Two assertions that cannot both hold for this persona make the case unpassable no matter what the agent does; the usual shape is one demanding information collection and another demanding an immediate exit.
- 2-4 per case. Include at least one that would catch a real regression.

Rules for personas:
- Give them a name and a concrete situation. Put anything the agent will need to collect into `facts`.
- Vary them. A suite where every caller is a cooperative 40-year-old booking a physical tests almost nothing."""


async def generate_suite(
    config: AgentConfig, count: int = 6, llm: Optional[LLMClient] = None
) -> list[TestCase]:
    """Derive a starter suite from the graph itself."""
    llm = llm or copilot_llm()
    graph = json.dumps(config.to_dict(), indent=2)
    prompt = f"""Here is the agent under test:

```json
{graph}
```

Write {count} test cases for it. Cover every distinct path through the graph, then spend any remaining cases on realistic friction that this specific graph looks likely to mishandle."""

    raw = await llm.structured(
        system=GEN_SYSTEM,
        prompt=prompt,
        schema=SUITE_SCHEMA,
        tool_name="write_cases",
        description="Return the generated test cases.",
        max_tokens=12000,
    )
    return [
        TestCase.from_dict({**c, "id": f"tc_{uuid.uuid4().hex[:8]}", "origin": "generated"})
        for c in raw.get("cases", [])
    ]
