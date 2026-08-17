#
# An in-memory calendar, for the test suite.
#
# This is not a stub that returns canned answers — it is a small working
# calendar with real overlap rules, because the point is to test the *agent*
# against a calendar that behaves like one. A fake that always says yes would
# let every double-booking bug through.
#
# It also exists so the suite never touches a real calendar. A test run that
# creates events in someone's Google account is worse than no test run, and
# it's the kind of mistake you only make once.
#

import uuid
from datetime import datetime, timedelta
from typing import Optional

from .base import Busy, CalendarError, Event, SlotTaken


class FakeCalendar:
    """A calendar that lives in a dict. Same interface as the real one."""

    def __init__(self, events: Optional[list] = None):
        self.events: dict[str, Event] = {}
        # Every write, in order — what a test asserts against when it wants to
        # know whether the agent actually did the thing it said it did.
        self.writes: list[dict] = []
        self._idempotency: dict[str, str] = {}
        for e in events or []:
            self.seed(**e)

    # ---- setup -------------------------------------------------------------
    def seed(self, start, end, summary: str = "Existing appointment", **kw) -> Event:
        """Put an appointment in the diary before the call starts."""
        event = Event(
            id=kw.get("id") or f"evt_{uuid.uuid4().hex[:8]}",
            start=_dt(start),
            end=_dt(end),
            summary=summary,
            description=kw.get("description", ""),
            attendees=kw.get("attendees", []),
        )
        self.events[event.id] = event
        return event

    @property
    def live(self) -> list[Event]:
        return [e for e in self.events.values() if not e.cancelled]

    # ---- provider interface ------------------------------------------------
    async def free_busy(self, calendar_id: str, start: datetime, end: datetime) -> list[Busy]:
        return [
            Busy(e.start, e.end)
            for e in self.live
            if e.end > start and e.start < end
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
        # A retry must not produce a second appointment. Callers repeat
        # themselves and networks time out after succeeding.
        if idempotency_key and idempotency_key in self._idempotency:
            return self.events[self._idempotency[idempotency_key]]

        for e in self.live:
            if e.end > start and e.start < end:
                raise SlotTaken(f"{start.isoformat()} is already booked")

        event = Event(
            id=f"evt_{uuid.uuid4().hex[:8]}",
            start=start,
            end=end,
            summary=summary,
            description=description,
        )
        self.events[event.id] = event
        if idempotency_key:
            self._idempotency[idempotency_key] = event.id
        self.writes.append({"op": "create", "event": event.to_dict()})
        return event

    async def find_events(
        self, calendar_id: str, start: datetime, end: datetime, query: str = ""
    ) -> list[Event]:
        needle = (query or "").lower()
        return [
            e
            for e in self.live
            if e.end > start
            and e.start < end
            and (not needle or needle in f"{e.summary} {e.description}".lower())
        ]

    async def cancel_event(self, calendar_id: str, event_id: str) -> None:
        event = self.events.get(event_id)
        if event is None or event.cancelled:
            raise CalendarError(f"No event '{event_id}' to cancel")
        event.cancelled = True
        self.writes.append({"op": "cancel", "event": event.to_dict()})

    async def move_event(
        self, calendar_id: str, event_id: str, start: datetime, end: datetime
    ) -> Event:
        event = self.events.get(event_id)
        if event is None or event.cancelled:
            raise CalendarError(f"No event '{event_id}' to move")
        for other in self.live:
            if other.id != event_id and other.end > start and other.start < end:
                raise SlotTaken(f"{start.isoformat()} is already booked")
        event.start, event.end = start, end
        self.writes.append({"op": "move", "event": event.to_dict()})
        return event

    # ---- assertions --------------------------------------------------------
    def summary(self) -> str:
        """The diary as evidence, for a judge or a failing test."""
        if not self.live:
            return "(the calendar is empty)"
        lines = []
        for e in sorted(self.live, key=lambda x: x.start):
            lines.append(f"- {e.start.isoformat()} → {e.end.isoformat()}  {e.summary}")
        cancelled = [e for e in self.events.values() if e.cancelled]
        for e in cancelled:
            lines.append(f"- CANCELLED  {e.start.isoformat()}  {e.summary}")
        return "\n".join(lines)


def _dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
