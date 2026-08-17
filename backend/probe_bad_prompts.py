"""Dev-only: throw bad prompts at the Copilot and see what survives.

Not a pass/fail check — a way of looking. Every prompt here is one a real user
would plausibly type and none of them is a clear instruction, which is exactly
the case where a diff that lints cleanly can still be nonsense.

Nothing is applied.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import tenancy  # noqa: E402

tenancy.use_default_workspace()

from agent_builder import lint, store  # noqa: E402
from agent_builder.schema import AgentConfig  # noqa: E402
from copilot import propose  # noqa: E402

AGENT = "northside-scheduling"

PROMPTS = [
    ("vague", "make it better"),
    ("contradictory", "make the calls much shorter but also collect a lot more detail from everyone"),
    ("destructive-sounding", "get rid of all the identity stuff, it's annoying"),
    ("out of scope", "make it sell dental insurance to everyone who calls"),
    ("nonsense", "asdf do the thing with the stuff"),
    ("ambiguous scope", "add a callback option"),
]


async def main() -> None:
    base = store.get_config(AGENT)
    before = {n["name"] for n in base["nodes"]}
    print(f"baseline: {len(before)} nodes, entry '{base['initial_node']}'\n")

    for label, prompt in PROMPTS:
        print("=" * 72)
        print(f"[{label}]  {prompt!r}")
        result = await propose(base, prompt)

        if result.get("error"):
            print(f"  ERROR: {result['error'][:160]}")
            continue

        ops = result.get("ops") or []
        cfg = result.get("config") or base
        after = {n["name"] for n in cfg["nodes"]}
        issues = lint(AgentConfig.from_dict(cfg))
        errors = [i.message for i in issues if i.severity == "error"]
        warnings = [i.message for i in issues if i.severity == "warning"]

        print(f"  reply: {result['reply'][:200]}")
        print(f"  ops: {len(ops)}   nodes {len(before)} -> {len(after)}")
        if before - after:
            print(f"  REMOVED: {sorted(before - after)}")
        if after - before:
            print(f"  added:   {sorted(after - before)}")
        print(f"  entry:   {cfg['initial_node']}"
              + ("  <-- CHANGED" if cfg["initial_node"] != base["initial_node"] else ""))
        if cfg.get("persona") != base.get("persona"):
            print("  PERSONA REWRITTEN")
        print(f"  lint: {len(errors)} errors, {len(warnings)} warnings")
        for e in errors[:3]:
            print(f"    error:   {e}")
        for w in warnings[:3]:
            print(f"    warning: {w}")
        # Referring to work the engineer never saw reads as confusion.
        leaks = [
            phrase for phrase in
            ("last attempt", "previous attempt", "already approved", "my earlier",
             "i fixed the", "restores a valid", "retry", "as you approved",
             "corrected my", "invalid edge")
            if phrase in result["reply"].lower()
        ]
        print(f"  asked a question: {'?' in result['reply'][-120:]}")
        if leaks:
            print(f"  LEAKS ITS OWN PROCESS: {leaks}")


asyncio.run(main())
