#
# Storing the grant, and turning it back into a usable calendar.
#
# A refresh token is a standing permission to read and write someone's diary.
# Supabase encrypts the disk, but that doesn't help against anything that can
# run a select, so tokens are encrypted at the application layer too — with a
# key derived from SESSION_SECRET, which already has to be present and secret
# for the app to issue sessions at all.
#
# Refresh happens lazily, on use, with a minute of slack. Refreshing on a timer
# would mean a background job that has to know about tenancy; refreshing on use
# means the token is valid exactly when something needs it.
#

import base64
import hashlib
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger

import db
import tenancy

from .google import GoogleCalendar, SCOPES, refresh_access_token

# Refresh a little before expiry: a token that dies mid-call is worse than one
# refreshed a minute early.
EXPIRY_SLACK = timedelta(seconds=90)


# ------------------------------------------------------------ encryption ---
def _fernet():
    from cryptography.fernet import Fernet

    secret = os.environ.get("SESSION_SECRET", "")
    if not secret:
        raise RuntimeError("SESSION_SECRET is not set; refusing to store calendar tokens.")
    key = base64.urlsafe_b64encode(hashlib.sha256(f"calendar:{secret}".encode()).digest())
    return Fernet(key)


def seal(value: str) -> str:
    return _fernet().encrypt((value or "").encode()).decode() if value else ""


def unseal(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except Exception:
        # Almost always a rotated SESSION_SECRET. Say so plainly rather than
        # surfacing a cryptography stack trace during a phone call.
        raise RuntimeError(
            "Stored calendar tokens can't be decrypted — SESSION_SECRET has "
            "changed. Reconnect the calendar."
        )


# ------------------------------------------------------------- storage ----
def get_connection(org_id: Optional[str] = None) -> Optional[dict]:
    org = org_id or tenancy.org()
    return db.one(
        """select org_id::text, provider, account_email, access_token, refresh_token,
                  expires_at, scopes, connected_by
             from calendar_connections where org_id = %s""",
        (org,),
    )


def save_connection(
    org_id: str,
    access_token: str,
    refresh_token: str,
    expires_in: int,
    account_email: str = "",
    connected_by: str = "",
    scopes: str = "",
) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in or 3600))
    db.execute(
        """insert into calendar_connections
             (org_id, provider, account_email, access_token, refresh_token,
              expires_at, scopes, connected_by, updated_at)
           values (%s,'google',%s,%s,%s,%s,%s,%s, now())
           on conflict (org_id) do update set
             account_email = excluded.account_email,
             access_token  = excluded.access_token,
             -- Google omits the refresh token on re-consent sometimes; keeping
             -- the old one is the difference between a working reconnect and a
             -- connection that silently expires in an hour.
             refresh_token = case when excluded.refresh_token = ''
                                  then calendar_connections.refresh_token
                                  else excluded.refresh_token end,
             expires_at = excluded.expires_at,
             scopes = excluded.scopes,
             connected_by = excluded.connected_by,
             updated_at = now()""",
        (
            org_id, account_email, seal(access_token), seal(refresh_token),
            expires_at, scopes or " ".join(SCOPES), connected_by,
        ),
    )
    logger.info(f"calendar connected for org {org_id[:8]} ({account_email})")


def disconnect(org_id: Optional[str] = None) -> None:
    db.execute(
        "delete from calendar_connections where org_id = %s", (org_id or tenancy.org(),)
    )


def status(org_id: Optional[str] = None) -> dict:
    row = get_connection(org_id)
    if not row:
        return {"connected": False}
    return {
        "connected": True,
        "email": row["account_email"],
        "connected_by": row["connected_by"],
        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
    }


# ---------------------------------------------------------- the provider ---
def provider_for(org_id: Optional[str] = None) -> GoogleCalendar:
    """A calendar client that refreshes its own token."""
    org = org_id or tenancy.org()

    async def token() -> str:
        row = get_connection(org)
        if not row:
            raise RuntimeError("No calendar is connected for this workspace.")

        expires_at = row["expires_at"]
        fresh = expires_at and expires_at - EXPIRY_SLACK > datetime.now(timezone.utc)
        if fresh and row["access_token"]:
            return unseal(row["access_token"])

        refresh = unseal(row["refresh_token"])
        if not refresh:
            raise RuntimeError(
                "The calendar connection has no refresh token — reconnect it."
            )
        body = await refresh_access_token(
            refresh,
            os.environ.get("GOOGLE_CLIENT_ID", ""),
            os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        )
        save_connection(
            org,
            body["access_token"],
            body.get("refresh_token", ""),
            body.get("expires_in", 3600),
            account_email=row["account_email"],
            connected_by=row["connected_by"],
            scopes=row["scopes"],
        )
        return body["access_token"]

    return GoogleCalendar(token)
