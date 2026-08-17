#
# AgentBuilder — loads a declarative agent (JSON / dict) and compiles its node
# graph into Pipecat Flows objects.
#
#   JSON  ->  AgentConfig (validated)  ->  Pipecat Flows NodeConfig graph
#
# This is the seam between "agent as data" (what the Copilot produces) and
# "agent as a running conversation" (what bot.py executes).
#
# `lint()` is the other half of that seam: a compiler for agent graphs. The UI
# renders its output as inline errors, and the Copilot reads it to self-correct
# before a patch is ever shown to a human — the same loop a coding agent runs
# when it compiles its own output.
#

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from loguru import logger

from .schema import AgentConfig, Edge, Node

# What OpenAI will accept in a function schema. Anything else is a 400 that
# takes the whole call down with it.
JSON_SCHEMA_TYPES = {"string", "number", "integer", "boolean", "array", "object", "null"}
E164_RE = re.compile(r"^\+\d{7,15}$")
TOOL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$")


@dataclass
class Issue:
    """One problem with an agent graph."""

    severity: str   # "error" | "warning"
    message: str
    node: str = ""      # node name this attaches to, for UI highlighting
    function: str = ""  # edge function name, when the issue is edge-scoped

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "message": self.message,
            "node": self.node,
            "function": self.function,
        }


def lint(config: AgentConfig) -> list[Issue]:
    """Return every structural problem with a graph. Never raises.

    Errors make an agent un-runnable. Warnings are things that compile but
    reliably produce bad calls — a dead end where the caller gets stuck, a node
    nothing can reach.
    """
    issues: list[Issue] = []
    names = [n.name for n in config.nodes]

    if not config.nodes:
        return [Issue("error", "Agent has no nodes.")]

    for dupe in {n for n in names if names.count(n) > 1}:
        issues.append(Issue("error", f"Duplicate node name '{dupe}'.", dupe))

    name_set = set(names)
    if config.initial_node not in name_set:
        issues.append(
            Issue("error", f"initial_node '{config.initial_node}' is not a defined node.")
        )

    def check_edge(edge: Edge, node_name: str) -> None:
        where = f"edge '{edge.function}'" + (f" on node '{node_name}'" if node_name else " (global)")
        if not TOOL_NAME_RE.match(edge.function or ""):
            issues.append(
                Issue(
                    "error",
                    f"{where}: function name must be letters, digits, _ or - (max 64 chars).",
                    node_name,
                    edge.function,
                )
            )
        if edge.target not in name_set:
            issues.append(
                Issue("error", f"{where} targets unknown node '{edge.target}'.", node_name, edge.function)
            )
        if not (edge.description or "").strip():
            issues.append(
                Issue(
                    "warning",
                    f"{where} has no description — the model won't know when to take it.",
                    node_name,
                    edge.function,
                )
            )
        for req in edge.required:
            if req not in edge.properties:
                issues.append(
                    Issue(
                        "error",
                        f"{where} requires '{req}' but never declares it in properties.",
                        node_name,
                        edge.function,
                    )
                )

        # An edge becomes a function schema sent to OpenAI, and OpenAI rejects a
        # malformed one with a 400 that kills the whole call. Verified against
        # the live API: a bogus type and a bare string are both refused. These
        # are errors rather than warnings because the agent simply cannot run.
        if not isinstance(edge.properties, dict):
            issues.append(
                Issue(
                    "error",
                    f"{where}: properties must be an object mapping each field to a schema.",
                    node_name,
                    edge.function,
                )
            )
        else:
            for field, spec in edge.properties.items():
                if not isinstance(spec, dict):
                    issues.append(
                        Issue(
                            "error",
                            f"{where}: field '{field}' must be a schema like "
                            f'{{"type": "string"}}, not {type(spec).__name__}.',
                            node_name,
                            edge.function,
                        )
                    )
                    continue
                declared = spec.get("type")
                if declared is None:
                    issues.append(
                        Issue(
                            "warning",
                            f"{where}: field '{field}' declares no type, so the model has "
                            "to guess what to put in it.",
                            node_name,
                            edge.function,
                        )
                    )
                elif declared not in JSON_SCHEMA_TYPES:
                    issues.append(
                        Issue(
                            "error",
                            f"{where}: field '{field}' has type '{declared}', which isn't a "
                            f"JSON Schema type. Use one of {', '.join(sorted(JSON_SCHEMA_TYPES))}.",
                            node_name,
                            edge.function,
                        )
                    )

        if edge.target == node_name and node_name:
            issues.append(
                Issue(
                    "warning",
                    f"{where} points back at its own node, so taking it changes nothing. "
                    "Collect the extra field on the node itself instead.",
                    node_name,
                    edge.function,
                )
            )

    # Two globals with the same name both reach the model, because only a node's
    # own edge shadows a global — a global can't shadow another global.
    global_names = [e.function for e in config.global_edges]
    for dupe in {n for n in global_names if global_names.count(n) > 1}:
        issues.append(
            Issue("error", f"Two global edges are both named '{dupe}'.", "", dupe)
        )

    for edge in config.global_edges:
        check_edge(edge, "")

    for node in config.nodes:
        fns = [e.function for e in node.edges]
        for dupe in {f for f in fns if fns.count(f) > 1}:
            issues.append(
                Issue("error", f"Node '{node.name}' has two edges named '{dupe}'.", node.name, dupe)
            )
        for edge in node.edges:
            check_edge(edge, node.name)

        if node.transfer_to:
            if not E164_RE.match(node.transfer_to):
                issues.append(
                    Issue(
                        "error",
                        f"Node '{node.name}': transfer_to '{node.transfer_to}' must be a "
                        "phone number in E.164 form, like +34722482770.",
                        node.name,
                    )
                )
            elif not node.end:
                # Handing the caller to a person is the end of our involvement;
                # a graph that expects them back is describing something the
                # telephony can't do.
                issues.append(
                    Issue(
                        "warning",
                        f"Node '{node.name}' transfers the caller to {node.transfer_to} but "
                        "isn't marked as ending the call. Once the call is handed over, "
                        "the agent can't take it back.",
                        node.name,
                    )
                )

        if not node.task_messages and not node.role_message:
            issues.append(
                Issue("warning", f"Node '{node.name}' has no instructions.", node.name)
            )
        if not node.end and not config.edges_for(node):
            issues.append(
                Issue(
                    "warning",
                    f"Node '{node.name}' is a dead end: no edges out and not marked as ending "
                    "the call. The caller will be stuck here.",
                    node.name,
                )
            )

    # Reachability from the entry point.
    if config.initial_node in name_set:
        seen = {config.initial_node}
        stack = [config.initial_node]
        while stack:
            current = config.node(stack.pop())
            if current is None:
                continue
            for edge in config.edges_for(current):
                if edge.target in name_set and edge.target not in seen:
                    seen.add(edge.target)
                    stack.append(edge.target)
        for node in config.nodes:
            if node.name not in seen:
                issues.append(
                    Issue("warning", f"Node '{node.name}' is unreachable from the start.", node.name)
                )

    if not any(n.end for n in config.nodes):
        issues.append(Issue("warning", "No node ends the call — every path runs forever."))

    return issues


class AgentBuilder:
    """Builds a runnable Pipecat Flows graph from a declarative AgentConfig."""

    def __init__(self, config: AgentConfig, on_transition=None):
        """`on_transition(function, target, args)` fires whenever an edge is taken.

        The live-call view uses it to light up the graph as the caller talks,
        which is also the fastest way to see that the agent took a path you
        didn't intend.
        """
        self.config = config
        self.on_transition = on_transition
        self._nodes_by_name = {n.name: n for n in config.nodes}
        self._validate()

    # ---- loading -----------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict) -> "AgentBuilder":
        return cls(AgentConfig.from_dict(data))

    @classmethod
    def from_json(cls, path: Union[str, Path]) -> "AgentBuilder":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    # ---- validation --------------------------------------------------------
    def _validate(self) -> None:
        """Refuse to build a graph that can't run. Warnings are the UI's problem."""
        errors = [i for i in lint(self.config) if i.severity == "error"]
        if errors:
            raise ValueError("; ".join(i.message for i in errors))

    # ---- compilation -------------------------------------------------------
    def build_initial_node(self):
        """Return the entry NodeConfig; downstream nodes are built lazily on transition."""
        return self._make_node(self._nodes_by_name[self.config.initial_node])

    def _make_node(self, node: Node):
        from pipecat_flows import NodeConfig

        node_config: NodeConfig = {
            "name": node.name,
            "role_message": node.role_message or self.config.persona,
            "task_messages": node.task_messages,
            "functions": [
                self._make_edge_function(edge) for edge in self.config.edges_for(node)
            ],
        }
        if node.pre_actions:
            node_config["pre_actions"] = node.pre_actions
        # Explicit post_actions win; otherwise a terminal node ends the call.
        if node.post_actions:
            node_config["post_actions"] = node.post_actions
        elif node.end:
            node_config["post_actions"] = [{"type": "end_conversation"}]
        return node_config

    def _make_edge_function(self, edge: Edge):
        from pipecat_flows import FlowsFunctionSchema, FlowManager

        async def handler(args: dict, flow_manager: "FlowManager"):
            # Persist what the caller gave us so later nodes can use it.
            flow_manager.state.update(args)
            logger.info(f"[{edge.function}] -> {edge.target} | collected: {args}")
            if self.on_transition:
                try:
                    self.on_transition(edge.function, edge.target, args)
                except Exception as exc:  # observation must never break the call
                    logger.warning(f"on_transition hook failed: {exc}")
            next_node = self._make_node(self._nodes_by_name[edge.target])
            return {"status": "success", **args}, next_node

        return FlowsFunctionSchema(
            name=edge.function,
            description=edge.description,
            properties=edge.properties,
            required=edge.required,
            handler=handler,
        )
