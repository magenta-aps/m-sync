# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from datetime import date, datetime
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event
from icalendar.enums import STATUS, TRANSP

DENMARK = ZoneInfo("Europe/Copenhagen")


def make_event(
    start: date,
    end: date,
    written_at: datetime,
    uid: str = "anne-meeting",
    status: STATUS | None = None,
    transparency: TRANSP | None = None,
) -> Event:
    """Anne's meeting, with a subject that must never leave Nextcloud."""
    return Event.new(
        uid=uid,
        start=start,
        end=end,
        stamp=written_at,
        summary="Private things",
        status=status,
        transparency=transparency,
    )


def make_series(
    start: datetime,
    end: datetime,
    written_at: datetime,
    uid: str = "weekly-series",
    frequency: str = "weekly",
    count: int | None = 3,
) -> Event:
    """Anne's meeting, repeated `count` times, or forever if None."""
    series = make_event(start, end, written_at, uid)
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
