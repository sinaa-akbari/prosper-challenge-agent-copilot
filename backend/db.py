#
# Postgres access, and the switch that decides whether we use it.
#
# The store modules kept their original file-backed implementations and grew a
# Postgres one beside them, chosen by STORE_BACKEND. That isn't indecision: the
# simulator and the test suite are the inner loop of this project, and being
# able to run them against a local directory with no network is worth keeping.
# It also means the migration is reversible, which is the only reason to trust
# running it against live data.
#
# Connections go through Supabase's session pooler. The direct db.<ref> host no
# longer resolves for new projects, which is worth writing down because the
# failure looks like DNS being broken rather than like a deliberate change.
#

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

import settings  # noqa: F401  — loads .env with the shell-wins rule

DB_URL = os.environ.get("SUPABASE_DB_URL", "")
DEFAULT_ORG = "00000000-0000-0000-0000-000000000001"

_pool = None
_pool_lock = threading.Lock()


def enabled() -> bool:
    """True when the app should read and write Postgres rather than files."""
    return os.environ.get("STORE_BACKEND", "files").lower() == "postgres" and bool(DB_URL)


def _make_pool():
    from psycopg_pool import ConnectionPool

    # A pool that can't reach Postgres should fail on use, not on import — the
    # CLI tools in here are expected to run without a database.
    return ConnectionPool(DB_URL, min_size=1, max_size=8, open=True, timeout=20)


def pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = _make_pool()
    return _pool


@contextmanager
def conn():
    with pool().connection() as c:
        yield c


@contextmanager
def cursor(commit: bool = True):
    with conn() as c:
        with c.cursor() as cur:
            yield cur
        if commit:
            c.commit()


def query(sql: str, params: tuple = ()) -> list[dict]:
    """Rows as dicts. Small result sets only — everything here is per-agent."""
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def one(sql: str, params: tuple = ()) -> Optional[dict]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> None:
    with cursor() as cur:
        cur.execute(sql, params)


def jsonb(value: Any) -> str:
    """psycopg won't adapt a dict to jsonb on its own; this is the explicit cast."""
    return json.dumps(value if value is not None else None)


def ping() -> str:
    row = one("select version() as v")
    return (row or {}).get("v", "")


def migrate(verbose: bool = True) -> None:
    """Apply every .sql in migrations/, in name order.

    Each file is written to be re-runnable, which is cheaper than a migration
    ledger at this size and removes the failure mode where the ledger and the
    schema disagree.
    """
    folder = Path(__file__).parent / "migrations"
    for path in sorted(folder.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        with conn() as c:
            with c.cursor() as cur:
                cur.execute(sql)
            c.commit()
        if verbose:
            print(f"  applied {path.name}")


def table_counts() -> dict:
    tables = [
        "orgs", "agents", "agent_versions", "test_cases", "test_runs",
        "calls", "issues", "decisions", "jobs",
    ]
    out = {}
    for t in tables:
        row = one(f"select count(*) as n from {t}")
        out[t] = (row or {}).get("n", 0)
    return out
