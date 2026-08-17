"""Dev-only: the graph linter, especially the edge rules.

The linter is what the Copilot self-corrects against, so a gap here shows up as
"the Copilot built me a broken agent". These cases are the ones that reach
OpenAI as a function schema: a malformed one is a 400 that takes the whole call
down, which is invisible until someone rings the number.

Each rejection below was verified against the live API before being made an
error rather than a warning.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agent_builder import lint  # noqa: E402
from agent_builder.schema import AgentConfig  # noqa: E402

PROBLEMS = []


def check(ok, msg):
    print(f"  {'ok  ' if ok else 'FAIL'}  {msg}")
    if not ok:
        PROBLEMS.append(msg)


def graph(nodes, globals_=None):
    return {"name": "t", "voice_id": "v", "model": "gpt-4o", "persona": "p",
            "initial_node": "a", "nodes": nodes, "global_edges": globals_ or []}


def node(name, edges=None, end=False):
    return {"name": name, "task_messages": [{"role": "developer", "content": "x"}],
            "edges": edges or [], "end": end}


def edge(fn, target, **kw):
    return {"function": fn, "description": kw.pop("desc", "when the caller asks"),
            "target": target, **kw}


def severity(cfg) -> str:
    issues = lint(AgentConfig.from_dict(cfg))
    if any(i.severity == "error" for i in issues):
        return "error"
    return "warning" if issues else "clean"


def main() -> None:
    print("schemas OpenAI would reject are errors")
    for label, props in [
        ("a bare string instead of a schema", {"who": "string"}),
        ("a type that isn't a JSON Schema type", {"when": {"type": "datetime"}}),
    ]:
        cfg = graph([node("a", [edge("go", "b", properties=props)]), node("b", end=True)])
        check(severity(cfg) == "error", label)

    cfg = graph([node("a", [edge("go", "b", properties=["who"])]), node("b", end=True)])
    check(severity(cfg) == "error", "properties that isn't an object at all")

    cfg = graph([node("a", [edge("go", "b", properties={}, required=["who"])]),
                 node("b", end=True)])
    check(severity(cfg) == "error", "a required field that was never declared")

    print("\nduplicate tool names are errors")
    cfg = graph([node("a", [edge("go", "b")]), node("b", end=True)],
                [edge("help", "b"), edge("help", "b")])
    check(severity(cfg) == "error", "two global edges sharing a name")
    cfg = graph([node("a", [edge("go", "b"), edge("go", "b")]), node("b", end=True)])
    check(severity(cfg) == "error", "two edges on one node sharing a name")

    print("\nsloppy but runnable is a warning, not a block")
    cfg = graph([node("a", [edge("go", "b", properties={"w": {"description": "d"}})]),
                 node("b", end=True)])
    check(severity(cfg) == "warning", "a field with no declared type")
    cfg = graph([node("a", [edge("loop", "a"), edge("go", "b")]), node("b", end=True)])
    check(severity(cfg) == "warning", "an edge pointing back at its own node")

    print("\na node's own edge may shadow a global — that's the documented rule")
    cfg = graph([node("a", [edge("help", "b")]), node("b", end=True)], [edge("help", "b")])
    check(severity(cfg) != "error", "a node edge and a global sharing a name is allowed")

    print("\nand a good graph stays clean")
    cfg = graph(
        [node("a", [edge("go", "b", properties={"w": {"type": "string"}}, required=["w"])]),
         node("b", end=True)],
        [edge("emergency", "b", properties={})],
    )
    check(severity(cfg) == "clean", "a valid graph reports nothing")

    print()
    if PROBLEMS:
        print("PROBLEMS:")
        for p in PROBLEMS:
            print("  -", p)
        sys.exit(1)
    print("Lint checks passed.")


main()
