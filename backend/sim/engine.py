#
# Headless flow simulator — the agent graph, executed in text.
#
# WHY THIS EXISTS
# ---------------
# An agent you can only test by picking up the phone is an agent you test three
# times before shipping. The bottleneck in agent iteration isn't writing the
# change, it's knowing whether the change worked — and whether it broke the
# eleven conversations that already worked.
#
# This runs the exact same node graph against a simulated caller, with no audio
# in the loop. A full suite of a dozen conversations finishes in the time one
# real test call takes to say hello. That turns "does this work?" from a
# five-minute manual chore into something the Copilot can check by itself,
# before it ever shows you the diff.
#
# WHAT IT DELIBERATELY DOES NOT COVER
# -----------------------------------
# No audio means no STT errors, no barge-in, no latency, no prosody. Those are
# real failure modes and this harness is blind to all of them — the browser test
# call is still how you check that the agent *sounds* right. What it does cover
# is conversation logic: routing, collection, coverage, dead ends. In our
# experience that's where the overwhelming majority of iteration time goes,
# which is why it's the half worth automating first.
#

import asyncio
from dataclasses import asdict, dataclass, field
from typing import Optional

from agent_builder.schema import AgentConfig
from llm import LLMClient, sim_llm


def agent_sim_llm(model: str) -> LLMClient:
    """The agent under test, driven for reproducibility rather than realism.

    A live call runs this same graph at the model's own default sampling; here
    it runs at temperature 0 so that a suite result which moves means the graph
    moved. That trades away some coverage of genuinely borderline behaviour —
    a deterministic run won't find the one-in-five phrasing that derails — but
    a gate you can't trust to be stable can't gate anything. Flakiness is worth
    hunting on purpose, not by accident on every run.
    """
    return LLMClient(model, temperature=0)

# A node transition emits no speech, so a chain of them can spin without the
# caller ever getting a turn. Cap it.
MAX_CONSECUTIVE_TRANSITIONS = 4


@dataclass
class Turn:
    speaker: str            # "agent" | "caller" | "transition"
    text: str
    node: str = ""          # node the agent was in when this happened
    function: str = ""      # transition only
    target: str = ""        # transition only
    args: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Persona:
    """The simulated caller."""

    description: str = "A patient calling the clinic."
    goal: str = "Book an appointment."
    facts: dict = field(default_factory=dict)
    style: str = "Speaks naturally, in short sentences."

    @classmethod
    def from_dict(cls, d: dict) -> "Persona":
        d = d or {}
        return cls(
            description=d.get("description", cls.description),
            goal=d.get("goal", cls.goal),
            facts=d.get("facts", {}) or {},
            style=d.get("style", cls.style),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def system_prompt(self) -> str:
        facts = (
            "\n".join(f"- {k}: {v}" for k, v in self.facts.items())
            if self.facts
            else "- (none beyond what's above)"
        )
        return f"""You are role-playing a person phoning a healthcare clinic. You are the CALLER, not the assistant.

WHO YOU ARE
{self.description}

WHAT YOU WANT
{self.goal}

FACTS ABOUT YOU — only volunteer these when you're actually asked:
{facts}

HOW YOU TALK
{self.style}

RULES
- Reply with ONLY the words you say out loud. No stage directions, no narration, no quotation marks.
- One or two sentences. This is a phone call, not an essay.
- Never break character, never mention being an AI, never help the assistant do its job.
- If the assistant asks something you have no fact for, answer the way a real person would — improvise something plausible and consistent with who you are.
- When your goal is met and the assistant has wrapped up, or the assistant says goodbye, or you decide to give up and hang up, reply with exactly: [HANGUP]"""


@dataclass
class SimResult:
    turns: list = field(default_factory=list)          # list[Turn]
    path: list = field(default_factory=list)           # node names visited, in order
    collected: dict = field(default_factory=dict)      # everything the edges gathered
    ended: bool = False                                # reached a terminal node
    end_reason: str = ""                               # terminal | hangup | max_turns | error
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "turns": [t.to_dict() for t in self.turns],
            "path": self.path,
            "collected": self.collected,
            "ended": self.ended,
            "end_reason": self.end_reason,
            "error": self.error,
            "transcript": self.transcript(),
        }

    def transcript(self, include_transitions: bool = True) -> str:
        """Readable transcript. Transitions are included because assertions
        routinely need to talk about them ("the agent took the reschedule path")."""
        lines = []
        for t in self.turns:
            if t.speaker == "agent":
                lines.append(f"AGENT: {t.text}")
            elif t.speaker == "caller":
                lines.append(f"CALLER: {t.text}")
            elif include_transitions:
                args = f" {t.args}" if t.args else ""
                lines.append(f"  [flow: {t.function} -> {t.target}{args}]")
        return "\n".join(lines)


def _agent_system_prompt(config: AgentConfig, node) -> str:
    """Mirror what AgentBuilder hands Pipecat: role message + this node's tasks."""
    role = node.role_message or config.persona
    return f"""{role}

# CURRENT STEP: {node.name}
{node.instructions()}

# HOW TO MOVE
When the conditions for one of your available functions are met, call it. Do not
describe the function or mention it to the caller. If nothing else applies, keep
talking to the caller to get what the step needs."""


def _tools_for(config: AgentConfig, node) -> list[dict]:
    return [
        {
            "name": e.function,
            "description": e.description,
            "input_schema": {
                "type": "object",
                "properties": e.properties or {},
                "required": e.required or [],
            },
        }
        for e in config.edges_for(node)
    ]


@dataclass
class AgentStep:
    """One agent turn: what it said, where it moved, what it collected."""

    turns: list = field(default_factory=list)      # list[Turn]
    node: str = ""                                  # node after the step
    collected: dict = field(default_factory=dict)   # newly collected values
    ended: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "turns": [t.to_dict() for t in self.turns],
            "node": self.node,
            "collected": self.collected,
            "ended": self.ended,
            "error": self.error,
        }


async def advance(
    config: AgentConfig,
    node_name: str,
    history: list,
    agent_llm: Optional[LLMClient] = None,
) -> AgentStep:
    """Take one agent turn from `node_name`, following any transitions it triggers.

    `history` is the conversation so far as [{role: user|assistant, content}].
    Stateless: the caller owns the transcript. Shared by the batch simulator and
    the interactive chat tester, so both exercise identical semantics.
    """
    agent_llm = agent_llm or agent_sim_llm(config.model)
    step = AgentStep(node=node_name)
    node = config.node(node_name)
    if node is None:
        step.error = f"node '{node_name}' does not exist"
        return step

    convo = list(history)

    for _ in range(MAX_CONSECUTIVE_TRANSITIONS):
        reply = await agent_llm.reply(
            system=_agent_system_prompt(config, node),
            messages=convo,
            tools=_tools_for(config, node),
            max_tokens=400,
        )

        if reply.text:
            step.turns.append(Turn("agent", reply.text, node.name))
            convo.append({"role": "assistant", "content": reply.text})

        if not reply.tool_calls:
            break

        call = reply.tool_calls[0]
        edge = next((e for e in config.edges_for(node) if e.function == call.name), None)
        if edge is None:  # model hallucinated a tool; treat as a no-op
            break

        step.collected.update(call.arguments)
        step.turns.append(
            Turn("transition", "", node.name, edge.function, edge.target, call.arguments)
        )
        nxt = config.node(edge.target)
        if nxt is None:
            step.error = f"edge '{edge.function}' targets missing node '{edge.target}'"
            return step
        node = nxt
        step.node = node.name

        if node.end:
            closing = await agent_llm.reply(
                system=_agent_system_prompt(config, node),
                messages=convo,
                tools=[],
                max_tokens=300,
            )
            if closing.text:
                step.turns.append(Turn("agent", closing.text, node.name))
            step.ended = True
            return step

        # Transitioned without speaking — loop so the new node talks.
        if reply.text:
            break

    step.node = node.name
    return step


async def simulate(
    config: AgentConfig,
    persona: Persona,
    max_turns: int = 14,
    agent_llm: Optional[LLMClient] = None,
    caller_llm: Optional[LLMClient] = None,
) -> SimResult:
    """Run one full conversation between the agent graph and a simulated caller."""
    agent_llm = agent_llm or agent_sim_llm(config.model)
    caller_llm = caller_llm or sim_llm()

    result = SimResult()
    node = config.node(config.initial_node)
    if node is None:
        result.error = f"initial_node '{config.initial_node}' does not exist"
        result.end_reason = "error"
        return result

    result.path.append(node.name)
    history: list[dict] = []          # agent-side view: user=caller, assistant=agent
    caller_history: list[dict] = []   # caller-side view, mirrored

    try:
        for _ in range(max_turns):
            # --- agent's turn (may chain transitions before it speaks) ------
            step = await advance(config, node.name, history, agent_llm)
            if step.error:
                result.error = step.error
                result.end_reason = "error"
                return result

            spoke = False
            for turn in step.turns:
                result.turns.append(turn)
                if turn.speaker == "agent":
                    spoke = True
                    history.append({"role": "assistant", "content": turn.text})
                    caller_history.append({"role": "user", "content": turn.text})
                elif turn.speaker == "transition":
                    result.path.append(turn.target)

            result.collected.update(step.collected)
            node = config.node(step.node) or node

            if step.ended:
                result.ended = True
                result.end_reason = "terminal"
                return result

            if not spoke:
                result.turns.append(
                    Turn("agent", "(the agent said nothing)", node.name)
                )
                history.append({"role": "assistant", "content": "..."})
                caller_history.append({"role": "user", "content": "..."})

            # --- caller's turn ----------------------------------------------
            caller = await caller_llm.reply(
                system=persona.system_prompt(),
                messages=caller_history or [{"role": "user", "content": "(the phone connects)"}],
                max_tokens=200,
            )
            said = (caller.text or "").strip()
            if not said or "[HANGUP]" in said.upper():
                result.end_reason = "hangup"
                return result

            result.turns.append(Turn("caller", said, node.name))
            history.append({"role": "user", "content": said})
            caller_history.append({"role": "assistant", "content": said})

        result.end_reason = "max_turns"
        return result

    except Exception as exc:  # a broken graph shouldn't take down a whole run
        result.error = f"{type(exc).__name__}: {exc}"
        result.end_reason = "error"
        return result


async def simulate_many(configs_personas: list, concurrency: int = 6) -> list:
    """Run simulations in parallel, bounded. `configs_personas` is [(config, persona, max_turns)]."""
    sem = asyncio.Semaphore(concurrency)

    async def one(cfg, persona, turns):
        async with sem:
            return await simulate(cfg, persona, max_turns=turns)

    return await asyncio.gather(*(one(c, p, t) for c, p, t in configs_personas))
