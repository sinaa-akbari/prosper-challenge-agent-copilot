#
# What a calendar is, as far as this product is concerned.
#
# Five operations, one interface, three implementations: Google for production,
# Fake for the test suite, and whatever comes next for the customer who runs
# Microsoft 365. Binding the graph directly to Google would have made "we also
# support Outlook" a rewrite rather than a file.
#
# The slot arithmetic lives here rather than in a provider, because it is pure
# logic — busy intervals plus opening hours minus what's taken — and it is the
# part most likely to be wrong. Keeping it out of the network layer means it can
# be tested without a network.
#

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Optional, Protocol
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Busy:
    """An interval the calendar is not free. Always timezone-aware."""

    start: datetime
    end: datetime


@dataclass
class Event:
    id: str
    start: datetime
    end: datetime
    summary: str = ""
    description: str = ""
    attendees: list = field(default_factory=list)
    cancelled: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "summary": self.summary,
            "description": self.description,
            "attendees": self.attendees,
            "cancelled": self.cancelled,
        }


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime

    def spoken(self, tz: ZoneInfo) -> str:
        """How the agent should say it out loud.

        Always day *and* date *and* time. "Ten o'clock" on its own is how a
        caller ends up arriving on the wrong day — a real complaint sitting in
        this project's own call history.

        Built by hand rather than with strftime: the no-pad directives differ
        between platforms (`%-d` on Linux, `%#d` on Windows) and this string is
        read aloud to patients, so a stray leading zero is a stray "oh".
        """
        local = self.start.astimezone(tz)
        hour12 = local.hour % 12 or 12
        meridiem = "am" if local.hour < 12 else "pm"
        clock = f"{hour12}:{local.minute:02d}{meridiem}" if local.minute else f"{hour12}{meridiem}"
        return (
            f"{local.strftime('%A')} the {_ordinal(local.day)} of "
            f"{local.strftime('%B')} at {clock}"
        )


def _ordinal(day: int) -> str:
    """1st, 2nd, 3rd… including the 11th-13th exceptions everyone forgets."""
    if 11 <= day <= 13:
        return f"{day}th"
    return f"{day}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th') }".replace(" ", "")


class CalendarError(RuntimeError):
    """Anything the caller should hear about rather than crash on."""


class SlotTaken(CalendarError):
    """The slot went between offering it and booking it."""


class CalendarProvider(Protocol):
    """Every calendar this product can talk to."""

    async def free_busy(
        self, calendar_id: str, start: datetime, end: datetime
    ) -> list[Busy]: ...

    async def create_event(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
        summary: str,
        description: str = "",
        idempotency_key: str = "",
    ) -> Event: ...

    async def find_events(
        self, calendar_id: str, start: datetime, end: datetime, query: str = ""
    ) -> list[Event]: ...

    async def cancel_event(self, calendar_id: str, event_id: str) -> None: ...

    async def move_event(
        self, calendar_id: str, event_id: str, start: datetime, end: datetime
    ) -> Event: ...


# ------------------------------------------------------------------ slots ---
@dataclass
class OpeningHours:
    """When the clinic actually answers the door.

    Without this, free/busy says 3am on Sunday is available — which is true and
    useless. Days are Monday=0, matching datetime.weekday().
    """

    days: tuple = (0, 1, 2, 3, 4)
    opens: time = time(9, 0)
    closes: time = time(17, 30)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "OpeningHours":
        d = d or {}
        def parse(value, fallback):
            try:
                hh, mm = str(value).split(":")
                return time(int(hh), int(mm))
            except Exception:
                return fallback
        return cls(
            days=tuple(d.get("days", (0, 1, 2, 3, 4))),
            opens=parse(d.get("opens"), time(9, 0)),
            closes=parse(d.get("closes"), time(17, 30)),
        )


def free_slots(
    busy: list[Busy],
    *,
    tz: ZoneInfo,
    duration_minutes: int = 30,
    hours: Optional[OpeningHours] = None,
    now: Optional[datetime] = None,
    days_ahead: int = 14,
    limit: int = 8,
    lead_minutes: int = 60,
) -> list[Slot]:
    """Bookable slots, in clinic time, soonest first.

    `lead_minutes` exists because a slot forty seconds from now is technically
    free and nobody can make it.
    """
    hours = hours or OpeningHours()
    now = (now or datetime.now(tz)).astimezone(tz)
    earliest = now + timedelta(minutes=lead_minutes)
    step = timedelta(minutes=duration_minutes)

    # Normalising once beats comparing across zones on every candidate.
    taken = sorted(
        (Busy(b.start.astimezone(tz), b.end.astimezone(tz)) for b in busy),
        key=lambda b: b.start,
    )

    def overlaps(start: datetime, end: datetime) -> bool:
        for b in taken:
            if b.start >= end:
                break                      # sorted, so nothing later can overlap
            if b.end > start:
                return True
        return False

    out: list[Slot] = []
    day = earliest.date()
    for _ in range(days_ahead):
        if day.weekday() in hours.days:
            cursor = datetime.combine(day, hours.opens, tzinfo=tz)
            closing = datetime.combine(day, hours.closes, tzinfo=tz)
            while cursor + step <= closing:
                end = cursor + step
                if cursor >= earliest and not overlaps(cursor, end):
                    out.append(Slot(cursor, end))
                    if len(out) >= limit:
                        return out
                cursor = end
        day = day + timedelta(days=1)
    return out
