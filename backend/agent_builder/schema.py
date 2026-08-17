#
# Agent schema — the declarative contract the Copilot reads and writes.
#
# Design rule: stay as close to Pipecat Flows' own vocabulary as possible. A node
# carries Pipecat's native fields (`role_message`, `task_messages`, `pre/post_actions`)
# verbatim. The ONLY things we add are:
#
#   1. `edges` — transitions expressed as DATA (a string `target`) rather than as
#      Python closures, because a Copilot can emit a string, not a callable.
#   2. `global_edges` — edges appended to every non-terminal node. Real callers
#      interrupt the script ("wait, do you take my insurance?") from anywhere, and
#      without this the only fix is copy-pasting the same edge onto twenty nodes.
#      This is the single most common repair the Copilot makes, so it needs to be
#      one operation, not twenty.
#
# `AgentBuilder` turns these back into the closures Pipecat wants.
#

from dataclasses import dataclass, field
from typing import Optional

# ElevenLabs "Sarah". Deliberately not "Rachel" (21m00Tcm4TlvDq8ikWAM): Rachel is
# a voice-library entry, and library voices are paid-plan only. On a free key the
# TTS socket still connects and reports a time-to-first-byte, then returns no
# audio — a call that looks healthy and is silent. Sarah is a default voice and
# works on every plan. See `probe_voice()` in voice.py, which catches this class
# of failure and surfaces it instead of letting it pass as silence.
DEFAULT_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"
DEFAULT_MODEL = "gpt-4o"


@dataclass
class Edge:
    """A transition out of a node, exposed to the LLM as a callable tool."""

    function: str            # tool name the LLM calls to take this edge
    description: str         # when the model should call it
    target: str              # node to transition to (by name)
    # Fields to collect on this edge, as JSON-schema properties.
    properties: dict = field(default_factory=dict)
    required: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Edge":
        return cls(
            function=d["function"],
            description=d["description"],
            target=d["target"],
            properties=d.get("properties", {}),
            required=d.get("required", []),
        )

    def to_dict(self) -> dict:
        out = {
            "function": self.function,
            "description": self.description,
            "target": self.target,
        }
        if self.properties:
            out["properties"] = self.properties
        if self.required:
            out["required"] = self.required
        return out


@dataclass
class Node:
    """A single conversational state. Fields mirror Pipecat Flows' NodeConfig."""

    name: str
    task_messages: list = field(default_factory=list)   # this node's objectives
    role_message: Optional[str] = None                  # overrides the global persona
    edges: list = field(default_factory=list)           # list[Edge]; transitions out
    pre_actions: list = field(default_factory=list)
    post_actions: list = field(default_factory=list)
    end: bool = False                                   # terminal -> ends the call
    # A phone number, in E.164, to hand the caller to when this node is reached.
    # Until this existed, "I'll pass you to a member of the team" was a sentence
    # the agent said before hanging up on someone — the node claimed a capability
    # the product did not have. On a PSTN call this now dials a real person.
    transfer_to: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        return cls(
            name=d["name"],
            task_messages=d.get("task_messages", []),
            role_message=d.get("role_message"),
            edges=[Edge.from_dict(e) for e in d.get("edges", [])],
            pre_actions=d.get("pre_actions", []),
            post_actions=d.get("post_actions", []),
            end=d.get("end", False),
            transfer_to=(d.get("transfer_to") or None),
        )

    def to_dict(self) -> dict:
        out: dict = {"name": self.name, "task_messages": self.task_messages}
        if self.role_message:
            out["role_message"] = self.role_message
        if self.edges:
            out["edges"] = [e.to_dict() for e in self.edges]
        if self.pre_actions:
            out["pre_actions"] = self.pre_actions
        if self.post_actions:
            out["post_actions"] = self.post_actions
        if self.end:
            out["end"] = True
        if self.transfer_to:
            out["transfer_to"] = self.transfer_to
        return out

    # Convenience for the instruction text of a node, flattened for prompts.
    def instructions(self) -> str:
        return "\n".join(
            m.get("content", "") for m in self.task_messages if isinstance(m, dict)
        ).strip()


@dataclass
class AgentConfig:
    """A complete agent: identity + the conversation graph."""

    name: str
    initial_node: str
    nodes: list                          # list[Node]
    persona: str = ""                    # global role_message, applied to every node
    voice_id: str = DEFAULT_VOICE_ID
    model: str = DEFAULT_MODEL
    global_edges: list = field(default_factory=list)   # list[Edge], on every node

    @classmethod
    def from_dict(cls, d: dict) -> "AgentConfig":
        return cls(
            name=d["name"],
            initial_node=d["initial_node"],
            nodes=[Node.from_dict(n) for n in d["nodes"]],
            persona=d.get("persona", ""),
            voice_id=d.get("voice_id", DEFAULT_VOICE_ID),
            model=d.get("model", DEFAULT_MODEL),
            global_edges=[Edge.from_dict(e) for e in d.get("global_edges", [])],
        )

    def to_dict(self) -> dict:
        out = {
            "name": self.name,
            "voice_id": self.voice_id,
            "model": self.model,
            "persona": self.persona,
            "initial_node": self.initial_node,
            "nodes": [n.to_dict() for n in self.nodes],
        }
        if self.global_edges:
            out["global_edges"] = [e.to_dict() for e in self.global_edges]
        return out

    def node(self, name: str) -> Optional[Node]:
        return next((n for n in self.nodes if n.name == name), None)

    def edges_for(self, node: Node) -> list:
        """Every edge available at a node: its own, plus the globals.

        Terminal nodes get no globals — the call is ending, and offering an
        escape hatch there just gives the model a way to not hang up.
        """
        if node.end:
            return list(node.edges)
        own = {e.function for e in node.edges}
        # A node's own edge wins over a global of the same name.
        return list(node.edges) + [e for e in self.global_edges if e.function not in own]
