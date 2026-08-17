"""Call analysis: read production transcripts in bulk and cluster them into
ranked, evidenced issues the Copilot can act on directly."""

from .miner import (
    issue_context,
    load_calls,
    load_issues,
    mine_issues,
    save_calls,
    save_issues,
    set_issue_status,
)

__all__ = [
    "load_calls",
    "save_calls",
    "load_issues",
    "save_issues",
    "set_issue_status",
    "mine_issues",
    "issue_context",
]
