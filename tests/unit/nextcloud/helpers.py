# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event
from icalendar.enums import STATUS, TRANSP

DENMARK = ZoneInfo("Europe/Copenhagen")
NINE = datetime(2026, 10, 1, 9, tzinfo=UTC)
TEN = datetime(2026, 10, 1, 10, tzinfo=UTC)
# When Anne wrote her events. Fixed, so the same test data gives the same text.
WRITTEN_AT = datetime(2026, 9, 1, 8, tzinfo=UTC)


def make_event(
    uid: str = "anne-meeting",
    start: date = NINE,
    end: date = TEN,
    status: STATUS | None = None,
    transparency: TRANSP | None = None,
) -> Event:
    """Anne's meeting, with a subject that must never leave Nextcloud."""
    return Event.new(
        uid=uid,
        start=start,
        end=end,
        stamp=WRITTEN_AT,
        summary="Private things",
        status=status,
        transparency=transparency,
    )


def make_series(
    uid: str = "weekly-series",
    frequency: str = "weekly",
    count: int | None = 3,
    start: datetime = NINE,
    end: datetime = TEN,
) -> Event:
    """Anne's meeting, repeated `count` times, or forever if None."""
    series = make_event(uid, start, end)
    rule = {"freq": frequency} if count is None else {"freq": frequency, "count": count}
    series.add("rrule", rule)  # pyright: ignore[reportUnknownMemberType]
    return series


def make_calendar(*events: Event) -> Calendar:
    calendar = Calendar()
    for event in events:
        calendar.add_component(event)
    return calendar


def make_ics(*events: Event) -> str:
    return make_calendar(*events).to_ical().decode()
