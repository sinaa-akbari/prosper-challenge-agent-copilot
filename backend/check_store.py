"""Dev-only checks for the versioned store, against whichever backend is configured.

The invariant worth protecting is narrow and load-bearing: **an agent's current
config must equal the history row at its current version.** Everything the
Copilot is allowed to do rests on being able to read back and revert any change,
and a pointer that disagrees with its own history silently removes that.

It broke once already. `store.save()` computed the next version from a separate
read, and `on conflict do nothing` swallowed the collision — so a re-run
migration and a revert produced a v8 pointer and a v8 history row holding
different graphs.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import db  # noqa: E402
from agent_builder import store  # noqa: E402
import tenancy  # noqa: E402

tenancy.use_default_workspace()

AGENT = "_storetest"
PROBLEMS = []


def check(ok, msg):
    print(f"  {'ok  ' if ok else 'FAIL'}  {msg}")
    if not ok:
        PROBLEMS.append(msg)


def graph(n: int) -> dict:
    """A valid config with `n` nodes, so version rows are distinguishable."""
    nodes = [
        {
            "name": "greeting" if i == 0 else f"node_{i}",
            "task_messages": [{"role": "developer", "content": f"step {i}"}],
            "edges": [],
        }
        for i in range(n)
    ]
    return {
        "name": "Store test",
        "voice_id": "EXAVITQu4vr4xnSDxMaL",
        "model": "gpt-4o",
        "persona": "test",
        "initial_node": "greeting",
        "nodes": nodes,
    }


def consistent(agent_id: str) -> bool:
    rec = store.get(agent_id)
    return store.version_config(agent_id, rec["version"]) == rec["config"]


async def main():
    print(f"backend: {'postgres' if db.enabled() else 'files'}\n")
    store.delete(AGENT)

    print("versions are sequential and immutable")
    store.create(graph(1), agent_id=AGENT)
    for n in (2, 3, 4):
        store.save(AGENT, graph(n), label=f"grew to {n}")
    versions = store.versions(AGENT)
    check([v["version"] for v in versions] == [4, 3, 2, 1], "versions run 1..4 with no gaps")
    check(store.get(AGENT)["version"] == 4, "the pointer is at the newest version")
    check(consistent(AGENT), "the pointer's config matches its history row")
    check(
        len(store.version_config(AGENT, 2)["nodes"]) == 2,
        "an older version still holds the graph it was written with",
    )

    print("\nreverting is itself a version")
    rec = store.revert(AGENT, 2)
    check(rec["version"] == 5, f"revert appends rather than rewinding (v{rec['version']})")
    check(len(rec["config"]["nodes"]) == 2, "and restores the older graph")
    check(consistent(AGENT), "the pointer still matches its history row")
    check(
        len(store.version_config(AGENT, 4)["nodes"]) == 4,
        "the version that was reverted away from is untouched",
    )
    check(store.revert(AGENT, 5)["version"] == 6, "a revert can itself be undone")

    print("\nconcurrent saves can't collide on a version number")
    # The original bug in miniature: two writers that both read v6 and both
    # decide to write v7. One row used to be dropped silently.
    before = store.get(AGENT)["version"]
    await asyncio.gather(
        *[asyncio.to_thread(store.save, AGENT, graph(7 + i), f"race {i}") for i in range(4)]
    )
    after = store.versions(AGENT)
    numbers = sorted(v["version"] for v in after)
    check(
        numbers == list(range(1, before + 5)),
        f"every write got its own version (got {numbers})",
    )
    check(len(numbers) == len(set(numbers)), "no duplicated version numbers")
    check(consistent(AGENT), "the pointer matches its history row after the race")

    print("\nno two history rows share a version")
    if db.enabled():
        dup = db.one(
            """select count(*) as n from (
                 select version from agent_versions where agent_id = %s
                 group by version having count(*) > 1) d""",
            (AGENT,),
        )
        check(dup["n"] == 0, "the (agent, version) key holds")

    store.delete(AGENT)
    check(not store.exists(AGENT), "delete removes the agent")
    if db.enabled():
        left = db.one(
            "select count(*) as n from agent_versions where agent_id = %s", (AGENT,)
        )
        check(left["n"] == 0, "and its history goes with it")

    print()
    if PROBLEMS:
        print("PROBLEMS:")
        for p in PROBLEMS:
            print("  -", p)
        sys.exit(1)
    print("Store checks passed.")


asyncio.run(main())
