#
# Signing in.
#
# Phone OTP, because the people who operate a voice agent already trust a phone
# number more than a password, and because a builder that can edit a production
# call flow deserves something better than a shared secret in a browser popup.
#
# The delivery path is worth explaining, since it isn't the obvious one. Supabase
# Auth can send OTPs itself, but only through an SMS provider configured in its
# dashboard, and that config is currently broken — it has the Twilio *Account
# SID* where a sender should be, so every send fails with "Invalid From Number".
# Fixing it needs either a phone number we don't own or a dashboard change we
# can't make.
#
# So the backend drives the two halves separately:
#
#   * Twilio Verify sends and checks the code. Verify borrows Twilio's own sender
#     pool, so it works before the account owns a single number.
#   * Supabase stays the user store, reached through the GoTrue admin API with
#     the service key — which is exactly the key we were told to use, and never
#     leaves the server.
#
# The session is ours: an HMAC-signed cookie, HttpOnly, no database lookup on the
# hot path. Supabase's own JWT would mean either shipping the anon key to the
# browser or revalidating with GoTrue on every request, and neither is worth it
# when the only claim we need is "this phone number signed in".
#

import base64
import hashlib
import hmac
import json
import os
import re
import time
from typing import Optional
from urllib.parse import urlencode

import aiohttp
from loguru import logger

SESSION_COOKIE = "composer_session"
SESSION_TTL = 60 * 60 * 24 * 14        # a fortnight; this is a builder, not a bank

# Endpoints that must stay reachable without a session. Twilio can't log in, and
# the health check is what a load balancer reads.
# The Google callback lands here straight from accounts.google.com with no
# cookie of ours; it proves itself with a one-time state row instead.
OPEN_PREFIXES = (
    "/api/auth/",
    "/api/twilio/",
    "/api/health",
    "/api/integrations/google/callback",
)


class AuthError(Exception):
    """Something the caller should see, phrased for a human."""


# ---------------------------------------------------------------- config ---
def enabled() -> bool:
    """Auth is on unless explicitly disabled for local work."""
    return os.environ.get("AUTH_DISABLED", "").lower() not in ("1", "true", "yes")


def _secret() -> bytes:
    value = os.environ.get("SESSION_SECRET", "")
    if not value:
        # Refusing to run beats silently signing sessions with a default that
        # anyone reading this file could forge.
        raise AuthError("SESSION_SECRET is not set; refusing to issue sessions.")
    return value.encode()


# Anyone can sign up. The earlier allowlist made sense for a single-operator
# deployment and is wrong for a platform: it turned "create an account" into
# "ask an administrator to edit a file", which is not a product.
#
# Open signup moves the risk rather than removing it. An unauthenticated endpoint
# that sends SMS is the mechanism behind SMS pumping fraud — an attacker drives
# thousands of sends to premium-rate numbers they earn revenue on. Twilio Verify
# caps sends per destination; the throttle below caps them per caller, which is
# the half Twilio can't see.
OTP_MAX_PER_WINDOW = 5
OTP_WINDOW_SECONDS = 15 * 60


def throttled(bucket: str) -> bool:
    """True when this caller has asked for too many codes lately.

    The window boundary is computed in Python and passed as a plain parameter.
    The obvious version — `interval '%s seconds'` — puts a placeholder inside a
    SQL string literal, where whether it binds at all depends on how the driver
    chooses to send the query. It silently did nothing here: no error, no row,
    and a rate limiter that reported False forever. Placeholders belong outside
    quotes.
    """
    import db

    if not db.enabled():
        return False

    cutoff = time.time() - OTP_WINDOW_SECONDS
    try:
        row = db.one(
            """insert into otp_throttle (bucket, count, window_start)
               values (%s, 1, now())
               on conflict (bucket) do update set
                 count = case
                   when otp_throttle.window_start < to_timestamp(%s) then 1
                   else otp_throttle.count + 1 end,
                 window_start = case
                   when otp_throttle.window_start < to_timestamp(%s) then now()
                   else otp_throttle.window_start end
               returning count""",
            (bucket, cutoff, cutoff),
        )
    except Exception as exc:
        # Loudly: a throttle that fails open is a decision, not an accident, and
        # the only way anyone finds out is if it says so.
        logger.error(f"otp throttle failed open for {bucket}: {type(exc).__name__}: {exc}")
        return False
    return (row or {}).get("count", 0) > OTP_MAX_PER_WINDOW


def normalise(phone: str) -> str:
    """To E.164, or raise. Twilio is strict and its error text is unhelpful."""
    cleaned = re.sub(r"[^\d+]", "", phone or "")
    if not cleaned.startswith("+"):
        raise AuthError("Include the country code, starting with '+'.")
    if not re.fullmatch(r"\+\d{7,15}", cleaned):
        raise AuthError("That doesn't look like a phone number.")
    return cleaned


def signups_open() -> bool:
    """Whether a number nobody has seen before may create an account."""
    return os.environ.get("AUTH_OPEN_SIGNUP", "1").lower() not in ("0", "false", "no")


# --------------------------------------------------------------- session ---
def issue(
    subject: str,
    method: str = "phone",
    user_id: Optional[str] = None,
    org_id: Optional[str] = None,
) -> str:
    """Mint a session. The org travels in the cookie so every request knows
    whose data it may touch without a lookup on the hot path."""
    payload = {
        "sub": subject,
        "via": method,
        "uid": user_id,
        "org": org_id,
        "exp": int(time.time()) + SESSION_TTL,
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:40]
    return f"{body}.{sig}"


def read(token: Optional[str]) -> Optional[dict]:
    if not token or "." not in token:
        return None
    body, _, sig = token.rpartition(".")
    expected = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:40]
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


# ------------------------------------------------------------------ otp ---
def _verify_service() -> str:
    sid = os.environ.get("TWILIO_VERIFY_SERVICE_SID", "")
    if not sid:
        raise AuthError("Text-message login isn't configured on this deployment.")
    return sid


async def _verify_api(path: str, data: dict) -> dict:
    url = f"https://verify.twilio.com/v2/Services/{_verify_service()}{path}"
    auth = aiohttp.BasicAuth(
        os.environ.get("TWILIO_API_KEY_SID", ""),
        os.environ.get("TWILIO_API_KEY_SECRET", ""),
    )
    async with aiohttp.ClientSession() as s:
        async with s.post(
            url, auth=auth, data=urlencode(data),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=aiohttp.ClientTimeout(total=25),
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status >= 400:
                code = body.get("code")
                # 60200 is a malformed number and 60203 is too many sends; both
                # are the user's problem to fix and both deserve saying so.
                if code == 60200:
                    raise AuthError("Twilio rejected that number as invalid.")
                if code == 60203:
                    raise AuthError("Too many codes sent to that number. Wait a few minutes.")
                raise AuthError(body.get("message", f"Twilio error {resp.status}."))
            return body


async def send_code(phone: str) -> None:
    await _verify_api("/Verifications", {"To": phone, "Channel": "sms"})
    logger.info(f"login code sent to {phone[:-4]}****")


async def check_code(phone: str, code: str) -> bool:
    try:
        body = await _verify_api("/VerificationCheck", {"To": phone, "Code": code})
    except AuthError:
        # Twilio 404s when no verification is pending — someone guessing codes
        # at a number that was never sent one. That's a wrong code, not a
        # configuration problem, and saying so would confirm which numbers have
        # a live challenge against them.
        return False
    return body.get("status") == "approved"


# -------------------------------------------------------------- supabase ---
async def _supabase_user(phone: str) -> Optional[str]:
    """Find or create the Supabase auth user for a phone number.

    Supabase is the identity store — that's what it was chosen for — reached
    through the GoTrue admin API with the service key, which never leaves the
    server. No anon key is shipped to the browser.
    """
    base, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not base or not key:
        return None
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    bare = phone.lstrip("+")
    async with aiohttp.ClientSession() as s:
        # GoTrue's admin list has no phone filter, so this pages. Fine at this
        # size; swap for a `users` table lookup first once it isn't.
        async with s.get(
            f"{base}/auth/v1/admin/users?page=1&per_page=200",
            headers=headers, timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            users = (await resp.json(content_type=None)).get("users", []) if resp.status == 200 else []
        for u in users:
            if u.get("phone") == bare:
                return u.get("id")
        async with s.post(
            f"{base}/auth/v1/admin/users", headers=headers,
            json={"phone": phone, "phone_confirm": True},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status < 300:
                return (await resp.json(content_type=None)).get("id")
            body = await resp.text()
            logger.warning(f"supabase user create failed {resp.status}: {body[:160]}")
    return None


def _local_account(user_id: str, phone: str) -> Optional[str]:
    """Map the Supabase user to a workspace, creating one on first sight.

    Returns the org id. Everything the person goes on to build — agents, tests,
    calls — is stamped with it.
    """
    import db

    if not db.enabled():
        return None
    row = db.one("select org_id from users where id = %s", (user_id,))
    if row:
        db.execute("update users set last_seen = now() where id = %s", (user_id,))
        return str(row["org_id"])

    with db.conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "insert into orgs (name) values (%s) returning id",
                (f"{phone} workspace",),
            )
            org_id = str(cur.fetchone()[0])
            cur.execute(
                """insert into users (id, phone, org_id) values (%s, %s, %s)
                   on conflict (id) do update set last_seen = now()
                   returning org_id""",
                (user_id, phone, org_id),
            )
            org_id = str(cur.fetchone()[0])
            cur.execute(
                """insert into org_members (org_id, user_id, role) values (%s, %s, 'owner')
                   on conflict do nothing""",
                (org_id, user_id),
            )
        c.commit()
    logger.info(f"created a workspace for {phone[:-4]}****")
    return org_id


async def sign_in(phone: str) -> tuple[Optional[str], Optional[str]]:
    """Resolve a verified phone number to (user_id, org_id).

    Runs after the code has already been checked, so it can't be used to probe
    for accounts.
    """
    user_id = await _supabase_user(phone)
    if not user_id:
        return None, None
    try:
        return user_id, _local_account(user_id, phone)
    except Exception as exc:
        logger.warning(f"workspace provisioning failed for {phone[:-4]}****: {exc}")
        return user_id, None


def org_for_agent(agent_id: str) -> Optional[str]:
    """Which workspace owns an agent.

    Needed by the paths with no session: an inbound phone call belongs to
    whoever owns the agent that answered it.
    """
    try:
        import db

        if not db.enabled():
            return None
        row = db.one("select org_id from agents where id = %s", (agent_id,))
        return str(row["org_id"]) if row else None
    except Exception:
        return None


# ----------------------------------------------------------- break glass ---
def password_login(password: str) -> bool:
    """A way in when SMS can't be delivered.

    Deliberately here rather than pretended away: an OTP flow whose provider is
    misconfigured locks the owner out of their own deployment, and that has to
    have an answer that isn't 'ssh in'. Unset AUTH_PASSWORD to remove it once a
    phone number is on the allowlist.
    """
    expected = os.environ.get("AUTH_PASSWORD", "")
    return bool(expected) and hmac.compare_digest(password or "", expected)


def status() -> dict:
    """What the sign-in screen needs to know about its own options."""
    return {
        "enabled": enabled(),
        "phone": bool(os.environ.get("TWILIO_VERIFY_SERVICE_SID")),
        "signup": signups_open(),
        # A break-glass for the operator of the deployment, not a user-facing
        # option; the screen only offers it when SMS is unavailable.
        "password": bool(os.environ.get("AUTH_PASSWORD")),
    }
