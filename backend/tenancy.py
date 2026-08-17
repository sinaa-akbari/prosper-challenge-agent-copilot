#
# Who the current request belongs to.
#
# Every table already carried org_id; until now every write used the same
# constant, which is a single-tenant app wearing a multi-tenant schema. This is
# the piece that makes the column mean something.
#
# It's a ContextVar rather than an argument threaded through the store, because
# the alternative is adding `org_id` to forty functions and trusting that nobody
# ever forgets one — and the failure mode of forgetting is showing somebody
# else's call transcripts. A default that is wrong for everyone is safer than a
# default that silently works for the first tenant.
#
# asyncio.create_task copies the current context, so a background job keeps the
# org of the request that started it.
#

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

# No default. Reading this before anything set it is a bug, and it should look
# like one rather than quietly resolving to tenant zero.
_current_org: ContextVar[Optional[str]] = ContextVar("current_org", default=None)
_current_user: ContextVar[Optional[str]] = ContextVar("current_user", default=None)


class NoTenant(RuntimeError):
    """Raised when org-scoped data is touched outside a request."""


def set_context(org_id: Optional[str], user_id: Optional[str] = None) -> None:
    _current_org.set(org_id)
    _current_user.set(user_id)


def org() -> str:
    value = _current_org.get()
    if not value:
        raise NoTenant(
            "No organisation in context — this data is per-account and the "
            "request didn't establish who it belongs to."
        )
    return value


def org_or_none() -> Optional[str]:
    return _current_org.get()


def user() -> Optional[str]:
    return _current_user.get()


@contextmanager
def as_org(org_id: str, user_id: Optional[str] = None):
    """Run a block as a given tenant.

    For the paths that have no session to read: an inbound phone call belongs to
    whoever owns the agent that answered it, and the CLI tools act on an agent
    they were named explicitly.
    """
    org_token = _current_org.set(org_id)
    user_token = _current_user.set(user_id)
    try:
        yield
    finally:
        _current_org.reset(org_token)
        _current_user.reset(user_token)


def use_default_workspace() -> str:
    """For scripts: act on the workspace that owns the live agent.

    Command-line tools have no session, and the alternative to saying so
    explicitly is a fallback inside org() — which is exactly the silent default
    that let every account share one workspace in the first place.

    It follows the live agent rather than pinning to the original org, because
    agents move between accounts and a script that keeps pointing at an empty
    workspace reports "no data" instead of "wrong workspace".
    """
    import db

    org = db.DEFAULT_ORG
    try:
        import activation

        org = activation.active().get("org_id") or db.DEFAULT_ORG
    except Exception:
        pass
    set_context(org, None)
    return org
