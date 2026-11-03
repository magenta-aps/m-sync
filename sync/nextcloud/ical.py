# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

"""Reading events from Nextcloud's calendar objects.

We extract only when an event is and whether it takes time. Subject, place and
attendees are never read, so they cannot leak into logs or other systems.
"""

from datetime import UTC, date, datetime, tzinfo
from typing import cast

import recurring_ical_events  # pyright: ignore[reportMissingTypeStubs]
from icalendar import Calendar, Component, Event
from icalendar.tools import to_datetime

from ..model import (
    CalendarEvent,
    DatetimeInterval,
    FreeBusyStatus,
    FullDayInterval,
)
from .spans import Span

PRODID = "-//Magenta//msync//EN"

# How many occurrences of one series we sync, so a rule like FREQ=MINUTELY
# cannot flood us.
MAX_OCCURRENCES = 500

# Everything needed to expand a series again later, and nothing else.
_TIMING_PROPERTIES = (
    "UID",
    "DTSTART",
    "DTEND",
    "DURATION",
    "RRULE",
    "RDATE",
    "EXDATE",
    "RECURRENCE-ID",
    "STATUS",
    "TRANSP",
)


class TooManyOccurrencesError(Exception):
    """A series with too many occurrences. `first_occurrences` holds the first ones."""

    first_occurrences: list[CalendarEvent]

    def __init__(self, reason: str, first_occurrences: list[CalendarEvent]) -> None:
        super().__init__(reason)
        self.first_occurrences = first_occurrences


def read_events(
    calendar: Calendar, timezone: tzinfo, window_start: datetime, window_end: datetime
) -> list[CalendarEvent]:
    """The busy time in one calendar object, a series expanded inside the window
    since the shared model has no recurrence. Floating times are in `timezone`."""
    uid = _read_series_uid(calendar)
    if not is_recurring(calendar):
        return [_make_calendar_event(calendar.events[0], uid, timezone)]
    window = Span(start=window_start, end=window_end)
    return _expand_series(calendar, uid, timezone, window)


def is_recurring(calendar: Calendar) -> bool:
    return any(
        "RRULE" in event or "RDATE" in event or "RECURRENCE-ID" in event
        for event in calendar.events
    )


def read_upper_text(event: Event, property_name: str) -> str:
    """A property as upper-case text, or "" if it is not there."""
    value = cast(object, event.get(property_name))
    return "" if value is None else str(value).strip().upper()


def strip_to_timing(calendar: Calendar) -> Calendar:
    """The calendar object without subject, place, attendees or description."""
    timing_only_calendar = Calendar()
    for component in calendar.subcomponents:
        timing_only_component = _strip_component(component)
        if timing_only_component is not None:
            timing_only_calendar.add_component(timing_only_component)
    return timing_only_calendar


def _strip_component(component: Component) -> Component | None:
    """A timezone as it is, an event with its timing only, and nothing else."""
    if component.name == "VTIMEZONE":
        return component
    if isinstance(component, Event):
        return _keep_timing(component)
    return None


def _read_series_uid(calendar: Calendar) -> str:
    """The UID that every VEVENT in the object shares, as CalDAV requires."""
    uids = {str(cast(object, event.uid)) for event in calendar.events}
    if not uids:
        raise ValueError("calendar object has no VEVENT")
    if len(uids) > 1:
        raise ValueError("the VEVENTs of a calendar object have different UIDs")
    uid = uids.pop()
    if not uid:
        raise ValueError("VEVENT has no UID")
    return uid


def _expand_series(
    calendar: Calendar, uid: str, timezone: tzinfo, window: Span
) -> list[CalendarEvent]:
    # Dates are compared in the timezone of the bounds, so use the local one.
    series = recurring_ical_events.of(calendar, components=["VEVENT"])
    local_window_end = window.end.astimezone(timezone)
    occurrences: list[CalendarEvent] = []
    for occurrence in series.after(window.start.astimezone(timezone)):
        if not isinstance(occurrence, Event):
            break
        # A date, or a time without a timezone, is in `timezone`.
        occurrence_start = to_datetime(occurrence.start)
        local_start = occurrence_start.replace(
            tzinfo=occurrence_start.tzinfo or timezone
        )
        if local_start >= local_window_end:
            break
        # Only one more inside the window makes it too many.
        if len(occurrences) == MAX_OCCURRENCES:
            raise TooManyOccurrencesError(
                f"more than {MAX_OCCURRENCES} occurrences", occurrences
            )
        occurrences.append(_make_occurrence(occurrence, uid, timezone))
    return occurrences


def _make_occurrence(occurrence: Event, uid: str, timezone: tzinfo) -> CalendarEvent:
    original_id = f"{uid}/{_format_recurrence_id(occurrence)}"
    return _make_calendar_event(occurrence, original_id, timezone)


def _make_calendar_event(
    event: Event, original_id: str, timezone: tzinfo
) -> CalendarEvent:
    # icalendar refuses a DTSTART and DTEND of different kinds.
    start: date | datetime = event.start
    end: date | datetime = event.end
    if isinstance(start, datetime) and isinstance(end, datetime):
        # RFC 5545: a time without a timezone is local time.
        interval = DatetimeInterval(
            start=start.replace(tzinfo=start.tzinfo or timezone).astimezone(UTC),
            end=end.replace(tzinfo=end.tzinfo or timezone).astimezone(UTC),
        )
    else:
        interval = FullDayInterval(
            start=start,
            end=end,
        )
    return CalendarEvent(
        original_id=original_id,
        interval=interval,
        free_busy_status=_read_free_busy_status(event),
    )


def _read_free_busy_status(event: Event) -> FreeBusyStatus:
    # Read as text: the typed accessors raise on values other clients write.
    status = read_upper_text(event, "STATUS")
    if status == "CANCELLED" or read_upper_text(event, "TRANSP") == "TRANSPARENT":
        return FreeBusyStatus.FREE
    if status == "TENTATIVE":
        return FreeBusyStatus.TENTATIVE
    return FreeBusyStatus.BUSY


def _format_recurrence_id(event: Event) -> str:
    # RECURRENCE-ID is the occurrence's original start, so it survives moving it.
    recurrence_id_property = cast(object, event.RECURRENCE_ID)
    recurrence_id: date | datetime = (
        recurrence_id_property
        if isinstance(recurrence_id_property, date)
        else event.start
    )
    if not isinstance(recurrence_id, datetime):
        return recurrence_id.strftime("%Y%m%d")
    if recurrence_id.tzinfo is None:
        return recurrence_id.strftime("%Y%m%dT%H%M%S")
    return recurrence_id.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _keep_timing(event: Event) -> Event:
    timing_only_event = Event()
    for property_name in _TIMING_PROPERTIES:
        if property_name in event:
            timing_only_event[property_name] = event[property_name]
    return timing_only_event
