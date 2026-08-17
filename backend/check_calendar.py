"""Dev-only checks for slot arithmetic and the fake calendar.

None of this touches a network. The interesting failures in a calendar
integration are arithmetic — an appointment an hour out, a slot offered in the
past, two callers given the same time — and arithmetic can be tested exactly.
The Google adapter is deliberately thin so that almost nothing worth testing
lives behind an API call.
"""

import asyncio
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

from integrations.calendar import (  # noqa: E402
    Busy,
    FakeCalendar,
    OpeningHours,
    SlotTaken,
    free_slots,
)

MADRID = ZoneInfo("Europe/Madrid")
PROBLEMS = []


def check(ok, msg):
    print(f"  {'ok  ' if ok else 'FAIL'}  {msg}")
    if not ok:
        PROBLEMS.append(msg)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """A Monday-anchored helper: day 0 is Mon 6 Jan 2026."""
    return datetime(2026, 1, 6 + day, hour, minute, tzinfo=MADRID)


async def main() -> None:
    hours = OpeningHours(days=(0, 1, 2, 3, 4), opens=time(9, 0), closes=time(12, 0))
    now = at(0, 8, 0)          # Monday 08:00, before opening

    print("slots respect opening hours")
    slots = free_slots([], tz=MADRID, hours=hours, now=now, duration_minutes=30, limit=20, lead_minutes=0)
    first, last_today = slots[0], [s for s in slots if s.start.date() == at(0, 9).date()][-1]
    check(first.start == at(0, 9, 0), f"first slot is at opening ({first.start.time()})")
    check(last_today.end <= at(0, 12, 0), "nothing runs past closing")
    check(all(s.start.weekday() < 5 for s in slots), "no weekend slots")

    print("\nbusy time is excluded")
    busy = [Busy(at(0, 9, 0), at(0, 10, 0)), Busy(at(0, 10, 30), at(0, 11, 0))]
    slots = free_slots(busy, tz=MADRID, hours=hours, now=now, duration_minutes=30, limit=20, lead_minutes=0)
    today = [s for s in slots if s.start.date() == at(0, 9).date()]
    starts = [s.start.strftime("%H:%M") for s in today]
    check(starts == ["10:00", "11:00", "11:30"], f"free gaps only (got {starts})")
    check(
        not any(s.start < b.end and s.end > b.start for s in today for b in busy),
        "no slot overlaps a busy interval",
    )

    print("\nthe near future isn't offered")
    slots = free_slots([], tz=MADRID, hours=hours, now=at(0, 9, 5), duration_minutes=30, lead_minutes=60)
    check(slots[0].start >= at(0, 10, 5), f"lead time is honoured ({slots[0].start.time()})")
    check(all(s.start > at(0, 9, 5) for s in slots), "nothing in the past")

    print("\nslots are spoken with a day, not just a time")
    said = slots[0].spoken(MADRID)
    check(any(d in said for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")),
          f"the day is named: {said!r}")
    check(any(c.isdigit() for c in said), "and so is the time")

    print("\nthe fake calendar behaves like a calendar")
    cal = FakeCalendar()
    cal.seed(at(1, 10, 0), at(1, 10, 30), "Existing patient")
    busy = await cal.free_busy("primary", at(1, 0), at(2, 0))
    check(len(busy) == 1, "a seeded appointment shows as busy")

    booked = await cal.create_event("primary", at(1, 11, 0), at(1, 11, 30), "Maria Lopez")
    check(booked.id in cal.events, "booking creates an event")
    check(len(cal.live) == 2, "and leaves the existing one alone")

    print("\ndouble-booking is refused")
    try:
        await cal.create_event("primary", at(1, 10, 15), at(1, 10, 45), "Someone else")
        check(False, "an overlapping booking raises SlotTaken")
    except SlotTaken:
        check(True, "an overlapping booking raises SlotTaken")

    print("\na retry doesn't book twice")
    a = await cal.create_event("primary", at(2, 9, 0), at(2, 9, 30), "Retry", idempotency_key="k1")
    b = await cal.create_event("primary", at(2, 9, 0), at(2, 9, 30), "Retry", idempotency_key="k1")
    check(a.id == b.id, "the same key returns the same event")
    check(len([e for e in cal.live if e.summary == "Retry"]) == 1, "and only one exists")

    print("\ncancelling and moving")
    await cal.cancel_event("primary", booked.id)
    check(cal.events[booked.id].cancelled, "a cancelled event is marked, not deleted")
    check(booked.id not in [e.id for e in cal.live], "and drops out of the live diary")
    free_now = await cal.free_busy("primary", at(1, 11, 0), at(1, 11, 30))
    check(free_now == [], "its slot frees up again")

    moved = await cal.move_event("primary", a.id, at(2, 14, 0), at(2, 14, 30))
    check(moved.start == at(2, 14, 0), "an appointment can be moved")

    print("\nwrites are recorded for assertions")
    ops = [w["op"] for w in cal.writes]
    check(ops == ["create", "create", "cancel", "move"], f"every write is logged ({ops})")
    check("CANCELLED" in cal.summary(), "the summary shows cancellations as evidence")

    print("\nGoogle refuses a naive timestamp")
    from integrations.calendar.google import _iso
    from integrations.calendar.base import CalendarError

    try:
        _iso(datetime(2026, 1, 6, 10, 0))
        check(False, "a timestamp with no timezone is rejected")
    except CalendarError:
        check(True, "a timestamp with no timezone is rejected")
    check(_iso(at(0, 10)).endswith("+01:00"), "and an aware one carries its offset")

    print()
    if PROBLEMS:
        print("PROBLEMS:")
        for p in PROBLEMS:
            print("  -", p)
        sys.exit(1)
    print("Calendar checks passed.")


asyncio.run(main())
