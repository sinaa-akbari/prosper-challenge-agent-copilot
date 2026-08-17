#
# Google Calendar.
#
# A thin adapter over the v3 REST API — deliberately thin, because everything
# interesting (slot arithmetic, opening hours, what the agent says) lives in
# base.py where it can be tested without a network.
#
# Two things here are easy to get wrong and expensive to debug:
#
#   * Google only issues a refresh token on the *first* consent, unless the
#     authorize URL asks for offline access and forces the prompt. Miss it and
#     the integration works beautifully for one hour and then dies.
#   * Every timestamp must be sent with an explicit offset. Google will accept a
#     naive one and interpret it in the calendar's zone, which is how an
#     appointment lands an hour out and nobody can reproduce it.
#

import time as _time
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
from loguru import logger

from .base import Busy, CalendarError, Event, SlotTaken

API = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/auth"

# Read and write events, plus the calendar list so the user can pick one, plus
# their email so the UI can show which account is connected. Nothing wider —
# `calendar` (full) would also let us delete their calendars.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "openid",
    "email",
]


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    from urllib.parse import urlencode

    return AUTH_URL + "?" + urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            # Both are required to be handed a refresh token reliably.
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
    )


async def exchange_code(
    code: str, client_id: str, client_secret: str, redirect_uri: str
) -> dict:
    async with aiohttp.ClientSession() as s:
        async with s.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=aiohttp.ClientTimeout(total=25),
        ) as r:
            body = await r.json(content_type=None)
            if r.status >= 400:
                raise CalendarError(
                    f"Google refused the authorisation: {body.get('error_description') or body}"
                )
            return body


async def refresh_access_token(
    refresh_token: str, client_id: str, client_secret: str
) -> dict:
    async with aiohttp.ClientSession() as s:
        async with s.post(
            TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
            },
            timeout=aiohttp.ClientTimeout(total=25),
        ) as r:
            body = await r.json(content_type=None)
            if r.status >= 400:
                # A revoked grant is permanent — the user has to reconnect, and
                # saying so beats retrying forever.
                raise CalendarError(
                    f"Could not refresh Google access: {body.get('error_description') or body}"
                )
            return body


class GoogleCalendar:
    """One connected Google account.

    `token_source` is an async callable returning a valid access token; the
    caller owns refresh and storage, because tokens outlive any one request.
    """

    def __init__(self, token_source):
        self._token = token_source

    async def _request(self, method: str, path: str, **kw) -> dict:
        token = await self._token()
        async with aiohttp.ClientSession() as s:
            async with s.request(
                method,
                f"{API}{path}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=20),
                **kw,
            ) as r:
                if r.status == 204:
                    return {}
                body = await r.json(content_type=None)
                if r.status >= 400:
                    message = (body.get("error") or {}).get("message") or str(body)[:200]
                    if r.status == 409:
                        raise SlotTaken(message)
                    raise CalendarError(f"Google Calendar {r.status}: {message}")
                return body

    async def calendars(self) -> list[dict]:
        body = await self._request("GET", "/users/me/calendarList?maxResults=50")
        return [
            {
                "id": c["id"],
                "name": c.get("summary", c["id"]),
                "primary": bool(c.get("primary")),
                "timezone": c.get("timeZone", ""),
            }
            for c in body.get("items", [])
        ]

    async def free_busy(
        self, calendar_id: str, start: datetime, end: datetime
    ) -> list[Busy]:
        body = await self._request(
            "POST",
            "/freeBusy",
            json={
                "timeMin": _iso(start),
                "timeMax": _iso(end),
                "items": [{"id": calendar_id}],
            },
        )
        cal = (body.get("calendars") or {}).get(calendar_id) or {}
        if cal.get("errors"):
            raise CalendarError(f"Calendar '{calendar_id}': {cal['errors']}")
        return [
            Busy(datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
            for b in cal.get("busy", [])
        ]

    async def create_event(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
        summary: str,
        description: str = "",
        idempotency_key: str = "",
    ) -> Event:
        payload = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": _iso(start)},
            "end": {"dateTime": _iso(end)},
        }
        # Google accepts a client-supplied id and rejects a duplicate, which is
        # the cheapest idempotency available: a retry returns the same event
        # instead of a second appointment.
        if idempotency_key:
            payload["id"] = _event_id(idempotency_key)
        try:
            body = await self._request(
                "POST", f"/calendars/{calendar_id}/events", json=payload
            )
        except SlotTaken:
            if idempotency_key:
                existing = await self._request(
                    "GET", f"/calendars/{calendar_id}/events/{_event_id(idempotency_key)}"
                )
                return _event(existing)
            raise
        return _event(body)

    async def find_events(
        self, calendar_id: str, start: datetime, end: datetime, query: str = ""
    ) -> list[Event]:
        from urllib.parse import urlencode

        params = {
            "timeMin": _iso(start),
            "timeMax": _iso(end),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "20",
        }
        if query:
            params["q"] = query
        body = await self._request(
            "GET", f"/calendars/{calendar_id}/events?{urlencode(params)}"
        )
        return [
            _event(i)
            for i in body.get("items", [])
            if i.get("status") != "cancelled" and i.get("start", {}).get("dateTime")
        ]

    async def cancel_event(self, calendar_id: str, event_id: str) -> None:
        await self._request("DELETE", f"/calendars/{calendar_id}/events/{event_id}")

    async def move_event(
        self, calendar_id: str, event_id: str, start: datetime, end: datetime
    ) -> Event:
        body = await self._request(
            "PATCH",
            f"/calendars/{calendar_id}/events/{event_id}",
            json={"start": {"dateTime": _iso(start)}, "end": {"dateTime": _iso(end)}},
        )
        return _event(body)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise CalendarError(
            "Refusing to send a naive timestamp to Google — it would be read in "
            "the calendar's own zone and silently land an hour out."
        )
    return dt.isoformat()


def _event_id(key: str) -> str:
    """Google event ids allow base32hex characters only, and must be 5-1024 long."""
    import hashlib

    digest = hashlib.sha1(key.encode()).hexdigest()
    return "ac" + "".join(c for c in digest if c in "0123456789abcdefghijklmnopqrstuv")[:28]


def _event(raw: dict) -> Event:
    return Event(
        id=raw.get("id", ""),
        start=datetime.fromisoformat(raw["start"]["dateTime"]),
        end=datetime.fromisoformat(raw["end"]["dateTime"]),
        summary=raw.get("summary", ""),
        description=raw.get("description", ""),
        attendees=[a.get("email", "") for a in raw.get("attendees", [])],
        cancelled=raw.get("status") == "cancelled",
    )
