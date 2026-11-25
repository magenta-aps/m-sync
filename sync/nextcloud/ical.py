# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

"""Reading events from Nextcloud's calendar objects. Rendering and reading our
busy blocks. Rendering free/busy requests and reading the answers.

We extract only when an event is and whether it takes time. Subject, place and
attendees are never read, so they cannot leak into logs or other systems.
"""

import hashlib
from datetime import UTC, date, datetime, tzinfo
from itertools import chain
from typing import cast

import recurring_ical_events  # pyright: ignore[reportMissingTypeStubs]
import structlog
from icalendar import Calendar, Component, Event, FreeBusy, Timezone, vCalAddress
from icalendar.enums import STATUS, TRANSP
from icalendar.prop import vPeriod
from icalendar.tools import to_datetime

from ..model import (
    NEXTCLOUD,
    CalendarEvent,
    DatetimeInterval,
    EventSource,
    FreeBusyStatus,
    FullDayInterval,
    GroupwareSystemName,
    Interval,
)
from .spans import BusyPeriod, Span

logger = structlog.stdlib.get_logger()

_PRODID = "-//Magenta//msync//EN"

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

# Every busy block's name starts with this. Anything else is not ours.
_BUSY_BLOCK_PREFIX = "msync-busy-"

# Hex digits of the hash in a busy block's name: 128 bits.
_NAME_HASH_LENGTH = 32

# The source of the event a busy block shows. A busy block's name is a hash,
# so it cannot give the source back.
_SOURCE_SYSTEM_PROPERTY = "X-MSYNC-SOURCE-SYSTEM"
_SOURCE_ORIGINAL_ID_PROPERTY = "X-MSYNC-SOURCE-ORIGINAL-ID"

# The status of the event a busy block shows. STATUS and TRANSP cannot hold
# every status Core sends. An unknown status is written as busy, so it would
# read back as busy. A busy event that takes no time is written as
# transparent, so it would read back as free.
_FREE_BUSY_STATUS_PROPERTY = "X-MSYNC-FREE-BUSY-STATUS"


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
        source = EventSource(system=NEXTCLOUD, original_id=uid)
        return [_make_calendar_event(calendar.events[0], source, timezone)]
    window = Span(start=window_start, end=window_end)
    return _expand_series(calendar, uid, timezone, window)


def is_recurring(calendar: Calendar) -> bool:
    return any(
        "RRULE" in event or "RDATE" in event or "RECURRENCE-ID" in event
        for event in calendar.events
    )


def _read_upper_text(event: Event, property_name: str) -> str:
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
    source = EventSource(system=NEXTCLOUD, original_id=original_id)
    return _make_calendar_event(occurrence, source, timezone)


def _make_calendar_event(
    event: Event, source: EventSource, timezone: tzinfo
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
        source=source,
        interval=interval,
        free_busy_status=_read_free_busy_status(event),
    )


def _read_free_busy_status(event: Event) -> FreeBusyStatus:
    # Read as text: the typed accessors raise on values other clients write.
    status = _read_upper_text(event, "STATUS")
    if status == "CANCELLED" or _read_upper_text(event, "TRANSP") == "TRANSPARENT":
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


# A busy block is what we write to a ghost's calendar so its free/busy shows
# another system's busy time. There is one per source event with nothing private.
def make_busy_block_name(source: EventSource) -> str:
    """The object name of the busy block for one source event. The source id is
    hashed because it can contain "/" and be longer than the 255 characters
    Nextcloud allows for an object name."""
    hash_input = f"{source.system}:{source.original_id}".encode()
    name_hash = hashlib.sha256(hash_input).hexdigest()[:_NAME_HASH_LENGTH]
    return f"{_BUSY_BLOCK_PREFIX}{name_hash}.ics"


def render_busy_block(event: CalendarEvent, stamp: datetime) -> Calendar:
    """A busy block that says when the source event is and whether it takes
    time. `stamp` is when the busy block is written."""
    start, end = _make_busy_block_bounds(event.interval)
    if end < start:
        logger.warning(
            "event ends before it starts", original_id=event.get_source().original_id
        )
    # Nextcloud refuses an end equal to the start, so an event that takes no time
    # has no end. A date without an end is a whole day, so it is transparent.
    takes_time = end > start
    is_free = event.free_busy_status is FreeBusyStatus.FREE or not takes_time
    is_tentative = event.free_busy_status is FreeBusyStatus.TENTATIVE
    busy_event = Event.new(
        # The UID must equal the name. Nextcloud purges a trashed object with
        # the same UID on PUT, which is what lets the same busy block come back.
        uid=make_busy_block_name(event.get_source()).removesuffix(".ics"),
        stamp=stamp.astimezone(UTC),
        start=start,
        end=end if takes_time else None,
        summary="Busy",
        transparency=TRANSP.TRANSPARENT if is_free else TRANSP.OPAQUE,
        status=STATUS.TENTATIVE if is_tentative else STATUS.CONFIRMED,
    )
    # FIXME: icalendar cannot write a property value that has a line break in
    # it. An original_id like "abc\ndef" makes to_ical raise, so that busy
    # block is not written.
    busy_event.add(_SOURCE_SYSTEM_PROPERTY, event.get_source().system)  # pyright: ignore[reportUnknownMemberType]
    busy_event.add(_SOURCE_ORIGINAL_ID_PROPERTY, event.get_source().original_id)  # pyright: ignore[reportUnknownMemberType]
    busy_event.add(_FREE_BUSY_STATUS_PROPERTY, event.free_busy_status.value)  # pyright: ignore[reportUnknownMemberType]
    # Wrap the busy event in a calendar, which is what Nextcloud stores.
    calendar = Calendar()
    calendar.version = "2.0"
    calendar.prodid = _PRODID
    calendar.add_component(busy_event)
    return calendar


def read_busy_block(calendar: Calendar) -> CalendarEvent | None:
    """The source event a busy block we wrote shows. None if it is not one we
    can read."""
    # FIXME: Some events read back different from the source event. An event
    # that ends before it starts reads back with no length. A part of a second
    # is not written. Core updates these on every full sync.
    try:
        events = calendar.events
        if len(events) != 1:
            return None
        busy_event = events[0]
        source = _read_busy_block_source(busy_event)
        if source is None:
            return None
        # We always write UTC, so a time without a timezone is read as UTC.
        event = _make_calendar_event(busy_event, source, UTC)
    except ValueError:
        # Includes a missing DTSTART.
        return None
    interval = event.interval
    # iCalendar allows a full day event without DTEND and DURATION. We leave
    # both out on purpose when the source event takes no time.
    has_no_length = "DTEND" not in busy_event and "DURATION" not in busy_event
    if has_no_length and isinstance(interval, FullDayInterval):
        interval = FullDayInterval(start=interval.start, end=interval.start)
    status = _read_upper_text(busy_event, _FREE_BUSY_STATUS_PROPERTY).lower()
    return CalendarEvent(
        source=source,
        interval=interval,
        free_busy_status=(
            FreeBusyStatus(status)
            if status in FreeBusyStatus
            else event.free_busy_status
        ),
    )


def _read_busy_block_source(busy_event: Event) -> EventSource | None:
    system = cast(object, busy_event.get(_SOURCE_SYSTEM_PROPERTY))
    original_id = cast(object, busy_event.get(_SOURCE_ORIGINAL_ID_PROPERTY))
    # icalendar gives None when the property is missing. It gives a list when
    # the same property is there more than once. Neither is a busy block we
    # wrote.
    if not isinstance(system, str) or not isinstance(original_id, str):
        return None
    if not system or not original_id:
        return None
    return EventSource(system=GroupwareSystemName(system), original_id=original_id)


def read_busy_block_timestamp(calendar: Calendar) -> datetime | None:
    """The DTSTAMP of a busy block we wrote: when the busy block was written."""
    try:
        events = calendar.events
        timestamp = cast(object, events[0].DTSTAMP) if len(events) == 1 else None
    except (ValueError, KeyError):
        return None
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        return None
    return timestamp.astimezone(UTC)


def _make_busy_block_bounds(
    interval: Interval,
) -> tuple[date, date] | tuple[datetime, datetime]:
    """Full days as their dates and other intervals in UTC. A time in another
    timezone would need a VTIMEZONE."""
    if isinstance(interval, FullDayInterval):
        return interval.start, interval.end
    return (
        interval.start_datetime().astimezone(UTC),
        interval.end_datetime().astimezone(UTC),
    )


def render_calendar_timezone(timezone_id: str) -> Calendar:
    """A calendar that holds only the timezone, as the calendar-timezone
    property of a CalDAV calendar wants it (RFC 4791 5.2.2)."""
    calendar = Calendar()
    calendar.version = "2.0"
    calendar.prodid = _PRODID
    calendar.add_component(Timezone.from_tzid(timezone_id))
    return calendar


def render_free_busy_request(
    organizer_href: str, address: str, window: Span, timestamp: datetime
) -> Calendar:
    """A scheduling request for the free/busy of `address` in the window."""
    free_busy = FreeBusy.new(
        uid=f"msync-{window.start.astimezone(UTC):%Y%m%dT%H%M%SZ}",
        stamp=timestamp.astimezone(UTC),
        start=window.start.astimezone(UTC),
        end=window.end.astimezone(UTC),
        organizer=vCalAddress(organizer_href),
    )
    free_busy.add("attendee", vCalAddress(f"mailto:{address}"))  # pyright: ignore[reportUnknownMemberType]
    calendar = Calendar()
    calendar.version = "2.0"
    calendar.prodid = _PRODID
    calendar.method = "REQUEST"
    calendar.add_component(free_busy)
    return calendar


def read_busy_periods(calendar: Calendar) -> list[BusyPeriod]:
    """The busy and tentative periods in a free/busy answer."""
    # FREEBUSY holds one vPeriod for one period and a list for more.
    values: list[vPeriod | list[vPeriod]] = [
        component.get("FREEBUSY", [])
        for component in calendar.walk("VFREEBUSY")  # pyright: ignore[reportUnknownMemberType]
    ]
    periods = chain.from_iterable(
        value if isinstance(value, list) else [value] for value in values
    )
    busy_periods: list[BusyPeriod] = []
    for period in periods:
        free_busy_type = str(period.params.get("FBTYPE", "BUSY")).upper()  # pyright: ignore[reportAny]
        # FREE and BUSY-UNAVAILABLE (outside working hours) are not busy. Any
        # other type counts as BUSY, unknown ones too (RFC 5545 3.2.9).
        if free_busy_type in {"FREE", "BUSY-UNAVAILABLE"}:
            continue
        status = (
            FreeBusyStatus.TENTATIVE
            if free_busy_type == "BUSY-TENTATIVE"
            else FreeBusyStatus.BUSY
        )
        start, end_or_duration = period.dt
        end = (
            end_or_duration
            if isinstance(end_or_duration, datetime)
            else start + end_or_duration
        )
        # Without a timezone the instant is unknown, so converting it would
        # wrongly use the local timezone (RFC 5545 requires UTC here).
        if start.tzinfo is None or end.tzinfo is None:
            logger.error("skipping free/busy period without a timezone", period=period)
            continue
        busy_periods.append(
            BusyPeriod(
                start=start.astimezone(UTC), end=end.astimezone(UTC), status=status
            )
        )
    return busy_periods
