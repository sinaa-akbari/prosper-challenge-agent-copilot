#
# The LLM layer. One provider: OpenAI.
#
# Three jobs, three tiers, because they have genuinely different demands:
#
#   COPILOT_MODEL  writes graph patches, mines call logs, generates test suites.
#                  Reasoning-heavy, low volume, and the quality of the whole
#                  product rests on it — so it gets the flagship.
#   JUDGE_MODEL    grades transcripts against assertions. Medium volume, but a
#                  judge that is wrong is worse than no judge, so it isn't the
#                  cheapest tier.
#   SIM_MODEL      plays simulated callers. Highest volume by far and the
#                  easiest job — improvising a plausible person — so it's small
#                  and fast, which is what keeps a full suite run to seconds.
#
# The agent under test runs on whatever model its own config declares, so the
# harness measures the model that actually ships rather than a stand-in.
#
# Structured output is done with a forced tool call rather than a JSON mode:
# it validates against the schema on the way out and works identically on every
# model here.
#

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import settings  # noqa: F401  — loads .env with the shell-wins rule

COPILOT_MODEL = "gpt-5.1"
JUDGE_MODEL = "gpt-4.1"
SIM_MODEL = "gpt-4.1-mini"

# Models this project is expected to drive. Anything else is almost certainly a
# typo in an agent config, and a clear error beats a confusing 404 mid-call.
OPENAI_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")

# Reasoning models accept only the default temperature and reject any other
# value, so the setting has to be dropped rather than passed through for them.
FIXED_TEMPERATURE_PREFIXES = ("gpt-5", "o1", "o3", "o4")

# Paired with temperature 0 on the graded path. The value is arbitrary; holding
# it fixed is the point.
DETERMINISTIC_SEED = 20240917


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class Reply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMError(RuntimeError):
    pass


def is_supported_model(model: str) -> bool:
    return model.startswith(OPENAI_PREFIXES)


def _function(name: str, description: str, schema: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            },
        },
    }


class LLMClient:
    """A thin wrapper over OpenAI chat completions with tool calling."""

    def __init__(self, model: str, temperature: Optional[float] = None):
        if not is_supported_model(model):
            raise LLMError(
                f"'{model}' is not an OpenAI model. This project runs on OpenAI only — "
                f"use a gpt-* or o-series model id."
            )
        self.model = model
        # Dropped rather than rejected: a caller asking for temperature 0 wants
        # reproducibility, and a reasoning model refusing the request outright is
        # worse than quietly giving it the only temperature it has.
        if temperature is not None and model.startswith(FIXED_TEMPERATURE_PREFIXES):
            temperature = None
        self.temperature = temperature

    def _sampling(self) -> dict:
        if self.temperature is None:
            return {}
        # `seed` is best-effort on OpenAI's side rather than a guarantee, but it
        # removes enough of the remaining variance to be worth pinning.
        return {"temperature": self.temperature, "seed": DETERMINISTIC_SEED}

    def _client(self):
        from openai import AsyncOpenAI

        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMError("OPENAI_API_KEY is not set.")
        return AsyncOpenAI(api_key=key)

    # ---- conversational turn with optional tools ---------------------------
    async def reply(
        self,
        system: str,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        max_tokens: int = 1024,
    ) -> Reply:
        """One assistant turn. `messages` is [{role: user|assistant, content: str}]."""
        client = self._client()
        payload = [{"role": "system", "content": system}] + (
            messages or [{"role": "user", "content": "(call connected)"}]
        )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": payload,
            # Not `max_tokens`: the gpt-5 family rejects it outright, while every
            # model here accepts this one.
            "max_completion_tokens": max_tokens,
            **self._sampling(),
        }
        if tools:
            kwargs["tools"] = [
                _function(t["name"], t["description"], t["input_schema"]) for t in tools
            ]

        resp = await client.chat.completions.create(**kwargs)
        choice = resp.choices[0].message
        out = Reply(text=(choice.content or "").strip())
        for call in choice.tool_calls or []:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            out.tool_calls.append(ToolCall(call.function.name, args))
        return out

    # ---- structured output via a forced tool call --------------------------
    async def structured(
        self,
        system: str,
        prompt: str,
        schema: dict,
        tool_name: str = "respond",
        description: str = "Return the result.",
        max_tokens: int = 8000,
    ) -> dict:
        """Force the model to answer by filling in `schema`. Returns the arguments."""
        client = self._client()
        resp = await client.chat.completions.create(
            model=self.model,
            max_completion_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            tools=[_function(tool_name, description, schema)],
            tool_choice={"type": "function", "function": {"name": tool_name}},
            **self._sampling(),
        )
        message = resp.choices[0].message
        calls = message.tool_calls or []
        if not calls:
            raise LLMError(
                "Model returned no structured output "
                f"(finish_reason={resp.choices[0].finish_reason})."
            )
        raw = calls[0].function.arguments or "{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            # Nearly always a truncated response — say so, rather than surfacing
            # a bare parse error a hundred lines from the cause.
            raise LLMError(
                f"Structured output was not valid JSON ({exc}); "
                f"finish_reason={resp.choices[0].finish_reason}. "
                "If this is 'length', raise max_tokens for this call."
            ) from exc


def copilot_llm() -> LLMClient:
    return LLMClient(COPILOT_MODEL)


# The graded path runs at temperature 0. A suite whose result moves when nothing
# changed can't be used to decide whether a change is safe: "verify before apply"
# is only worth anything if a differing result means the graph differs. Sampling
# variety belongs in the personas, which are written per case, not in the dice.
def judge_llm() -> LLMClient:
    return LLMClient(JUDGE_MODEL, temperature=0)


def sim_llm() -> LLMClient:
    return LLMClient(SIM_MODEL, temperature=0)
