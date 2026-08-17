#
# Graph patches — the unit of change the Copilot emits.
#
# The Copilot never hands back a rewritten agent. It emits a list of typed
# operations against the existing graph, exactly the way a coding agent emits a
# diff instead of a rewritten file. That buys three things a whole-file rewrite
# can't:
#
#   * Review. A human can read six operations; nobody reads a 400-line JSON blob.
#   * Blast radius. Touching one node leaves the other nineteen byte-identical,
#     so a fix can't silently regress the parts of the agent that already work.
#   * Provenance. Every version records which ops produced it and why.
#
# Ops are applied to the plain dict form of an agent, so this module has no
# dependency on the runtime.
#

import copy
from typing import Any

# Node fields an `update_node` op is allowed to touch.
NODE_FIELDS = {"task_messages", "role_message", "pre_actions", "post_actions", "end"}


class PatchError(ValueError):
    """An operation that cannot be applied to this graph."""


def _find_node(cfg: dict, name: str) -> dict:
    for node in cfg["nodes"]:
        if node["name"] == name:
            return node
    raise PatchError(f"No node named '{name}'.")


def _edges(node: dict) -> list:
    return node.setdefault("edges", [])


def _rename_targets(cfg: dict, old: str, new: str) -> None:
    for node in cfg["nodes"]:
        for edge in node.get("edges", []):
            if edge.get("target") == old:
                edge["target"] = new
    for edge in cfg.get("global_edges", []):
        if edge.get("target") == old:
            edge["target"] = new
    if cfg.get("initial_node") == old:
        cfg["initial_node"] = new


def apply_ops(config: dict, ops: list[dict]) -> dict:
    """Apply operations in order, returning a new config. Never mutates the input."""
    cfg = copy.deepcopy(config)
    cfg.setdefault("nodes", [])
    cfg.setdefault("global_edges", [])

    for index, op in enumerate(ops):
        kind = op.get("op")
        try:
            _apply_one(cfg, kind, op)
        except PatchError:
            raise
        except KeyError as exc:
            raise PatchError(f"Operation {index} ({kind}) is missing field {exc}.") from exc
    return cfg


def _apply_one(cfg: dict, kind: str, op: dict) -> None:
    if kind == "set_meta":
        for key in ("name", "voice_id", "model", "persona"):
            if key in op:
                cfg[key] = op[key]

    elif kind == "set_initial_node":
        _find_node(cfg, op["name"])  # existence check
        cfg["initial_node"] = op["name"]

    elif kind == "add_node":
        node = op["node"]
        if any(n["name"] == node["name"] for n in cfg["nodes"]):
            raise PatchError(f"A node named '{node['name']}' already exists.")
        node.setdefault("task_messages", [])
        cfg["nodes"].append(node)

    elif kind == "update_node":
        node = _find_node(cfg, op["name"])
        patch: dict[str, Any] = op.get("patch", {})
        unknown = set(patch) - NODE_FIELDS
        if unknown:
            raise PatchError(
                f"update_node cannot set {sorted(unknown)}. "
                "Use add_edge/update_edge/delete_edge for edges, rename_node to rename."
            )
        node.update(patch)

    elif kind == "rename_node":
        node = _find_node(cfg, op["name"])
        new = op["new_name"]
        if new != op["name"] and any(n["name"] == new for n in cfg["nodes"]):
            raise PatchError(f"A node named '{new}' already exists.")
        node["name"] = new
        _rename_targets(cfg, op["name"], new)

    elif kind == "delete_node":
        name = op["name"]
        _find_node(cfg, name)
        cfg["nodes"] = [n for n in cfg["nodes"] if n["name"] != name]
        # Drop edges pointing at the deleted node so the graph stays valid.
        for node in cfg["nodes"]:
            node["edges"] = [e for e in node.get("edges", []) if e.get("target") != name]
        cfg["global_edges"] = [e for e in cfg["global_edges"] if e.get("target") != name]

    elif kind == "add_edge":
        node = _find_node(cfg, op["from"])
        edge = op["edge"]
        if any(e["function"] == edge["function"] for e in _edges(node)):
            raise PatchError(
                f"Node '{op['from']}' already has an edge named '{edge['function']}'."
            )
        _edges(node).append(edge)

    elif kind == "update_edge":
        node = _find_node(cfg, op["from"])
        target = next(
            (e for e in _edges(node) if e["function"] == op["function"]), None
        )
        if target is None:
            raise PatchError(f"Node '{op['from']}' has no edge '{op['function']}'.")
        target.update(op["edge"])

    elif kind == "delete_edge":
        node = _find_node(cfg, op["from"])
        before = len(_edges(node))
        node["edges"] = [e for e in _edges(node) if e["function"] != op["function"]]
        if len(node["edges"]) == before:
            raise PatchError(f"Node '{op['from']}' has no edge '{op['function']}'.")

    elif kind == "add_global_edge":
        edge = op["edge"]
        if any(e["function"] == edge["function"] for e in cfg["global_edges"]):
            raise PatchError(f"A global edge named '{edge['function']}' already exists.")
        cfg["global_edges"].append(edge)

    elif kind == "delete_global_edge":
        before = len(cfg["global_edges"])
        cfg["global_edges"] = [
            e for e in cfg["global_edges"] if e["function"] != op["function"]
        ]
        if len(cfg["global_edges"]) == before:
            raise PatchError(f"No global edge named '{op['function']}'.")

    else:
        raise PatchError(f"Unknown operation '{kind}'.")


def summarize(op: dict) -> str:
    """A one-line, human-readable rendering of an op, for the review UI."""
    kind = op.get("op")
    if kind == "add_node":
        return f"Add node '{op['node']['name']}'"
    if kind == "update_node":
        fields = ", ".join(op.get("patch", {}).keys()) or "nothing"
        return f"Edit node '{op['name']}' ({fields})"
    if kind == "rename_node":
        return f"Rename '{op['name']}' -> '{op['new_name']}'"
    if kind == "delete_node":
        return f"Delete node '{op['name']}'"
    if kind == "add_edge":
        return f"Add edge '{op['edge']['function']}': {op['from']} -> {op['edge']['target']}"
    if kind == "update_edge":
        return f"Edit edge '{op['function']}' on '{op['from']}'"
    if kind == "delete_edge":
        return f"Delete edge '{op['function']}' from '{op['from']}'"
    if kind == "add_global_edge":
        return f"Add global edge '{op['edge']['function']}' -> {op['edge']['target']} (available from every node)"
    if kind == "delete_global_edge":
        return f"Delete global edge '{op['function']}'"
    if kind == "set_initial_node":
        return f"Start the call at '{op['name']}'"
    if kind == "set_meta":
        return "Update " + ", ".join(k for k in op if k != "op")
    return kind or "unknown operation"


def affected_nodes(ops: list[dict]) -> dict[str, str]:
    """Map node name -> change kind ("added" | "removed" | "changed").

    The graph canvas uses this to tint nodes in the diff preview.
    """
    out: dict[str, str] = {}

    def mark(name: str, kind: str) -> None:
        # "added"/"removed" are stronger signals than "changed".
        if name and out.get(name) not in ("added", "removed"):
            out[name] = kind

    for op in ops:
        kind = op.get("op")
        if kind == "add_node":
            mark(op["node"]["name"], "added")
        elif kind == "delete_node":
            mark(op["name"], "removed")
        elif kind == "rename_node":
            mark(op["new_name"], "changed")
        elif kind in ("update_node", "set_initial_node"):
            mark(op["name"], "changed")
        elif kind in ("add_edge", "update_edge", "delete_edge"):
            mark(op["from"], "changed")
            if kind == "add_edge":
                mark(op["edge"]["target"], "changed")
    return out
