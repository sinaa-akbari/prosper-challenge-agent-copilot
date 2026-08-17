"""Simulation and evaluation: run an agent graph against synthetic callers in
text mode, then grade the transcripts against plain-English expectations."""

from .engine import AgentStep, Persona, SimResult, Turn, advance, simulate
from .judge import Verdict, judge
from .replay import case_from_call
from .suite import (
    TestCase,
    add_case,
    delete_case,
    generate_suite,
    load_cases,
    load_last_run,
    run_case,
    run_suite,
    save_cases,
)

__all__ = [
    "Persona",
    "SimResult",
    "Turn",
    "AgentStep",
    "advance",
    "simulate",
    "judge",
    "Verdict",
    "TestCase",
    "load_cases",
    "save_cases",
    "add_case",
    "delete_case",
    "run_case",
    "run_suite",
    "generate_suite",
    "load_last_run",
    "case_from_call",
]
