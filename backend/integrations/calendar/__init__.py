"""Calendar access: one interface, a real provider and a fake one.

The fake isn't a convenience — it's what stops the test suite writing
appointments into somebody's real diary, and what lets a graph that books
appointments be tested at all.
"""

from .base import (
    Busy,
    CalendarError,
    CalendarProvider,
    Event,
    OpeningHours,
    Slot,
    SlotTaken,
    free_slots,
)
from .fake import FakeCalendar

__all__ = [
    "Busy",
    "CalendarError",
    "CalendarProvider",
    "Event",
    "OpeningHours",
    "Slot",
    "SlotTaken",
    "free_slots",
    "FakeCalendar",
]
