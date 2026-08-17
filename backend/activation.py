#
# Which agent is live.
#
# Exactly one, deployment-wide. That isn't an arbitrary limit — there is one
# phone number, and a phone number rings one thing. Modelling it as a global
# single slot keeps the product honest: "active" means *this is what answers when
# someone calls*, not "enabled" in some vaguer sense that nobody can act on.
#
# It's global rather than per-account for the same reason. Two accounts can't
# both own the line, so activation is a claim that takes it from whoever had it,
# and the UI says so before you do it.
#
# When numbers become per-account, this becomes one row per number and the
# calling code barely changes — `active()` grows an argument.
#

import time
from typing import Optional

from loguru import logger

KEY = "active.agent"
LEGACY_KEY = "phone.assignment"     # what this was called before it had a name


def _read(key: str) -> dict:
    import db

    row = db.one("select value from app_settings where key = %s", (key,))
    return dict((row or {}).get("value") or {})


def active() -> dict:
    """{agent_id, org_id, activated_by, at} — or empty when nothing is live."""
    try:
        import db

        if not db.enabled():
            return {}
        return _read(KEY) or _read(LEGACY_KEY)
    except Exception as exc:
        logger.warning(f"could not read the active agent: {exc}")
        return {}


def active_agent_id() -> str:
    return active().get("agent_id", "")


def activate(agent_id: str, org_id: str, by: str = "") -> dict:
    """Make this the live agent, displacing whatever was live before."""
    import db

    previous = active()
    record = {
        "agent_id": agent_id,
        "org_id": org_id,
        "activated_by": by,
        "at": time.time(),
    }
    db.execute(
        """insert into app_settings (key, value, updated_at)
           values (%s, %s::jsonb, now())
           on conflict (key) do update set value = excluded.value, updated_at = now()""",
        (KEY, db.jsonb(record)),
    )
    # One slot, so there is nothing to switch off — but the displaced agent's
    # owner deserves to find out why their number went quiet, and a log line is
    # the least that costs.
    if previous.get("agent_id") and previous["agent_id"] != agent_id:
        logger.info(
            f"'{agent_id}' is now live, replacing '{previous['agent_id']}' "
            f"(org {previous.get('org_id')})"
        )
    else:
        logger.info(f"'{agent_id}' is now live")
    return record


def deactivate(agent_id: Optional[str] = None) -> None:
    """Take the agent off the line. With an id, only if it's the one that's live."""
    import db

    current = active()
    if agent_id and current.get("agent_id") != agent_id:
        return
    db.execute("delete from app_settings where key in (%s, %s)", (KEY, LEGACY_KEY))
    if current.get("agent_id"):
        logger.info(f"'{current['agent_id']}' is no longer live — the number won't answer")
