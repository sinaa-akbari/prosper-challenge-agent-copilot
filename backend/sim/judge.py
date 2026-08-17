#
# LLM-as-judge — turns a transcript into pass/fail against written expectations.
#
# Assertions are plain English ("the agent confirms the appointment time back to
# the caller") because that is how the people who own these agents actually
# describe correct behaviour. A deployment lead should be able to write a test
# without learning an assertion DSL.
#
# The judge is required to quote the transcript for every verdict. That's the
# guard against a confident-but-wrong grader: an unsupported claim has nowhere
# to hide when the evidence field has to contain real text from the call.
#

from dataclasses import dataclass, field
from typing import Optional

from llm import LLMClient, judge_llm

VERDICT_SCHEMA = {
    "properties": {
        "results": {
            "type": "array",
            "description": "One entry per assertion, in the order given.",
            "items": {
                "type": "object",
                "properties": {
                    "assertion": {"type": "string", "description": "The assertion, repeated verbatim."},
                    "passed": {"type": "boolean"},
                    "reason": {
                        "type": "string",
                        "description": "One sentence explaining the verdict.",
                    },
                    "evidence": {
                        "type": "string",
                        "description": (
                            "A short quote from the transcript that proves it. "
                            "If the assertion failed because something never happened, "
                            "say so explicitly instead of quoting."
                        ),
                    },
                },
                "required": ["assertion", "passed", "reason", "evidence"],
            },
        },
        "summary": {
            "type": "string",
            "description": "One or two sentences on how the call went overall.",
        },
    },
    "required": ["results", "summary"],
}

JUDGE_SYSTEM = """You grade transcripts of automated phone calls for a healthcare clinic.

You are given a transcript and a list of assertions about what the agent should have done. Judge each assertion independently and strictly against what the transcript actually shows.

Reading the transcript:
- `AGENT:` and `CALLER:` are spoken turns.
- `[flow: function -> node]` lines are internal state transitions. They are not spoken aloud, but they are real evidence about what the agent did — use them to judge routing and data collection.

Grading rules:
- Judge only what the transcript shows. Do not assume an unstated intention.
- An assertion about the agent *saying* something needs a spoken turn. An assertion about *routing* or *collecting* data can be satisfied by a flow line.
- Partial credit does not exist. If the assertion is only half true, it failed.
- A call that hit the turn limit without finishing has failed any assertion about completing the task.
- Be exacting but not pedantic: different wording that clearly conveys the required meaning passes."""


@dataclass
class AssertionResult:
    assertion: str
    passed: bool
    reason: str = ""
    evidence: str = ""

    def to_dict(self) -> dict:
        return {
            "assertion": self.assertion,
            "passed": self.passed,
            "reason": self.reason,
            "evidence": self.evidence,
        }


@dataclass
class Verdict:
    passed: bool = False
    results: list = field(default_factory=list)   # list[AssertionResult]
    summary: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "results": [r.to_dict() for r in self.results],
            "summary": self.summary,
            "error": self.error,
        }


async def judge(
    transcript: str,
    assertions: list[str],
    context: str = "",
    llm: Optional[LLMClient] = None,
) -> Verdict:
    """Grade one transcript against its assertions."""
    if not assertions:
        return Verdict(passed=True, summary="No assertions to check.")

    llm = llm or judge_llm()
    numbered = "\n".join(f"{i + 1}. {a}" for i, a in enumerate(assertions))
    prompt = f"""{('CONTEXT: ' + context) if context else ''}

TRANSCRIPT
----------
{transcript or '(the call produced no turns)'}

ASSERTIONS
----------
{numbered}

Return one result per assertion, in order."""

    try:
        raw = await llm.structured(
            system=JUDGE_SYSTEM,
            prompt=prompt,
            schema=VERDICT_SCHEMA,
            tool_name="report_verdict",
            description="Report pass/fail for every assertion.",
            max_tokens=4000,
        )
    except Exception as exc:
        return Verdict(passed=False, error=f"{type(exc).__name__}: {exc}")

    results = [
        AssertionResult(
            assertion=r.get("assertion", ""),
            passed=bool(r.get("passed")),
            reason=r.get("reason", ""),
            evidence=r.get("evidence", ""),
        )
        for r in raw.get("results", [])
    ]
    # A judge that silently returns fewer verdicts than assertions would show up
    # as a green suite. Treat any shortfall as a failure.
    if len(results) != len(assertions):
        for missing in assertions[len(results):]:
            results.append(
                AssertionResult(missing, False, "The judge did not return a verdict.", "")
            )

    return Verdict(
        passed=all(r.passed for r in results),
        results=results,
        summary=raw.get("summary", ""),
    )
