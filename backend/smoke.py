"""Smoke test for the AI pieces. `python smoke.py [sim|copilot|mine|all]`."""

import asyncio
import sys

from agent_builder import store
from agent_builder.schema import AgentConfig
from analysis import mine_issues
from copilot import propose
from sim import Persona, judge, simulate

AGENT_ID = "northside-scheduling"


async def test_sim():
    print("\n=== SIMULATOR ===")
    cfg = AgentConfig.from_dict(store.get_config(AGENT_ID))
    persona = Persona(
        description="Marcus Bell, 35, works a day shift at a warehouse.",
        goal="Book a blood-pressure follow-up, but only outside working hours.",
        facts={"name": "Marcus Bell", "dob": "July 14 1990", "reason": "blood pressure follow-up"},
        style="Polite but gets frustrated when not listened to.",
    )
    result = await simulate(cfg, persona, max_turns=8)
    print(result.transcript())
    print(f"\npath={result.path} end_reason={result.end_reason} err={result.error}")

    verdict = await judge(
        result.transcript(),
        [
            "The agent offers the caller an appointment time outside of standard working hours, or explains how to get one.",
            "The caller ends the call with an appointment booked.",
        ],
    )
    for r in verdict.results:
        print(f"  [{'PASS' if r.passed else 'FAIL'}] {r.assertion}\n         {r.reason}")


async def test_copilot():
    print("\n=== COPILOT ===")
    cfg = store.get_config(AGENT_ID)
    out = await propose(
        cfg,
        "Callers keep asking about insurance and the agent just ignores them. Add a way to handle that from anywhere in the call.",
    )
    print("reply:", out["reply"])
    print("error:", out["error"])
    for d in out["diff"]:
        print("  -", d["summary"])


async def test_mine():
    print("\n=== ISSUE MINER ===")
    cfg = store.get_config(AGENT_ID)
    issues = await mine_issues(AGENT_ID, cfg, persist=False)
    for i in issues:
        print(f"  [{i['severity']:8}] {i['title']}  ({i['call_count']} calls)")
        print(f"             {i['suggested_fix']}")


async def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("sim", "all"):
        await test_sim()
    if which in ("copilot", "all"):
        await test_copilot()
    if which in ("mine", "all"):
        await test_mine()


if __name__ == "__main__":
    asyncio.run(main())
