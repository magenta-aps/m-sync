# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import cast

import pytest
from icalendar import Calendar, Event, FreeBusy, vDDDTypes
from icalendar.enums import STATUS, TRANSP
from more_itertools import one
from structlog.testing import capture_logs

from sync.model import (
    MICROSOFT_CLOUD,
    NEXTCLOUD,
    CalendarEvent,
    DatetimeInterval,
    EventSource,
    FreeBusyStatus,
    FullDayInterval,
    Interval,
)
from sync.nextcloud.ical import (
    MAX_OCCURRENCES,
    TooManyOccurrencesError,
    make_busy_block_name,
    read_busy_block,
    read_busy_block_timestamp,
    read_busy_periods,
    read_events,
    render_busy_block,
    render_free_busy_request,
    strip_to_timing,
)
from sync.nextcloud.spans import BusyPeriod, Span
from tests.unit.nextcloud.helpers import (
    DENMARK,
    make_calendar,
    make_event,
    make_ics,
    make_series,
)

EXPANSION_START = datetime(2026, 10, 1, tzinfo=UTC)
EXPANSION_END = EXPANSION_START + timedelta(days=60)


def read_in_window(calendar: Calendar) -> list[CalendarEvent]:
    return read_events(calendar, DENMARK, EXPANSION_START, EXPANSION_END)


def test_a_start_time_without_timezone_uses_the_given_timezone() -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    # 09:00 Danish summer time on 1 October is 07:00 UTC.
    floating = make_event(
        start=datetime.fromisoformat("2026-10-01T09:00:00"),
        end=datetime.fromisoformat("2026-10-01T10:00:00"),
        written_at=written_at,
    )

    event = one(read_in_window(make_calendar(floating)))

    assert isinstance(event.interval, DatetimeInterval)
    assert event.interval.start == datetime(2026, 10, 1, 7, 0, tzinfo=UTC)


def test_an_event_starting_at_a_clock_time_and_ending_on_a_date_is_rejected() -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, tzinfo=UTC)
    # Event.new refuses this, but other clients write it.
    time_and_whole_date = make_event(t0, t1, written_at)
    del time_and_whole_date["DTEND"]
    time_and_whole_date.add("dtend", date(2026, 10, 2))  # pyright: ignore[reportUnknownMemberType]

    with pytest.raises(ValueError, match="same type"):
        _ = read_in_window(make_calendar(time_and_whole_date))


def test_an_all_day_event_is_a_full_day() -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    t0 = date(2026, 10, 1)
    t1 = date(2026, 10, 2)
    all_day = make_event(start=t0, end=t1, written_at=written_at)

    event = one(read_in_window(make_calendar(all_day)))

    assert isinstance(event.interval, FullDayInterval)
    assert event.interval.start == date(2026, 10, 1)
    assert event.interval.end == date(2026, 10, 2)


def test_read_events_applies_moved_and_cancelled_occurrences() -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    # A 09:00 standup, like the moved and cancelled occurrences below.
    t0 = datetime(2026, 10, 1, 9, tzinfo=UTC)
    t1 = datetime(2026, 10, 1, 10, tzinfo=UTC)
    standup = make_series(
        t0, t1, written_at, "daily-standup", frequency="daily", count=3
    )
    moved = Event.new(
        uid="daily-standup",
        recurrence_id=datetime(2026, 10, 2, 9, tzinfo=UTC),
        start=datetime(2026, 10, 2, 14, tzinfo=UTC),
        end=datetime(2026, 10, 2, 15, tzinfo=UTC),
    )
    cancelled = Event.new(
        uid="daily-standup",
        recurrence_id=datetime(2026, 10, 3, 9, tzinfo=UTC),
        start=datetime(2026, 10, 3, 9, tzinfo=UTC),
        end=datetime(2026, 10, 3, 10, tzinfo=UTC),
        status=STATUS.CANCELLED,
    )

    events = read_in_window(make_calendar(standup, moved, cancelled))

    assert [
        (
            event.source.original_id,  # pyright: ignore[reportOptionalMemberAccess]
            event.interval.start_datetime().hour,
            event.free_busy_status.value,
        )
        for event in events
    ] == [
        ("daily-standup/20261001T090000Z", 9, "busy"),
        ("daily-standup/20261002T090000Z", 14, "busy"),
        ("daily-standup/20261003T090000Z", 9, "free"),
    ]


def test_an_object_whose_events_have_different_uids_is_rejected() -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, tzinfo=UTC)
    other_meeting = Event.new(
        uid="other-meeting",
        recurrence_id=datetime(2026, 10, 8, 9, tzinfo=UTC),
        start=datetime(2026, 10, 8, 9, tzinfo=UTC),
        end=datetime(2026, 10, 8, 10, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="different UIDs"):
        _ = read_in_window(
            make_calendar(make_series(t0, t1, written_at), other_meeting)
        )


def test_a_series_is_cut_at_the_limit() -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, tzinfo=UTC)
    every_minute = make_series(
        t0, t1, written_at, "every-minute", frequency="minutely", count=None
    )

    with pytest.raises(TooManyOccurrencesError) as raised:
        _ = read_in_window(make_calendar(every_minute))

    assert len(raised.value.first_occurrences) == MAX_OCCURRENCES


@pytest.mark.parametrize("count", [MAX_OCCURRENCES - 1, MAX_OCCURRENCES])
def test_a_series_up_to_the_limit_is_read_whole(count: int) -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, tzinfo=UTC)
    hourly = make_series(
        t0, t1, written_at, "hourly-series", frequency="hourly", count=count
    )

    assert len(read_in_window(make_calendar(hourly))) == count


def test_one_more_than_the_limit_is_cut() -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, tzinfo=UTC)
    hourly = make_series(
        t0, t1, written_at, "hourly-series", "hourly", count=MAX_OCCURRENCES + 1
    )

    with pytest.raises(TooManyOccurrencesError) as raised:
        _ = read_in_window(make_calendar(hourly))

    assert len(raised.value.first_occurrences) == MAX_OCCURRENCES


def test_timing_only_keeps_what_expanding_needs_and_nothing_private() -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, tzinfo=UTC)
    series = make_series(t0, t1, written_at)
    series.exdates = [datetime(2026, 10, 8, 9, tzinfo=UTC)]
    series.location = "Home"
    calendar = make_calendar(series)

    timing_only = strip_to_timing(calendar)

    timing_only_ics = timing_only.to_ical().decode()
    assert "Private" not in timing_only_ics and "Home" not in timing_only_ics
    assert read_in_window(timing_only) == read_in_window(calendar)


def test_timing_only_keeps_status_and_transparency() -> None:
    series = Event.new(
        uid="weekly-series",
        start=datetime(2026, 10, 1, 9, tzinfo=UTC),
        end=datetime(2026, 10, 1, 10, tzinfo=UTC),
        status=STATUS.TENTATIVE,
        transparency=TRANSP.TRANSPARENT,
    )
    series.add("rrule", {"freq": "weekly"})  # pyright: ignore[reportUnknownMemberType]

    timing_only_event = one(strip_to_timing(make_calendar(series)).events)

    assert timing_only_event.status == "TENTATIVE"  # pyright: ignore[reportAny]
    assert timing_only_event.transparency == "TRANSPARENT"  # pyright: ignore[reportAny]


def test_a_calendar_read_from_text_gives_the_same_events() -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, tzinfo=UTC)
    calendar = make_calendar(make_series(t0, t1, written_at))

    from_text = Calendar.from_ical(make_ics(make_series(t0, t1, written_at)))

    assert read_in_window(from_text) == read_in_window(calendar)


@pytest.mark.parametrize("status", [FreeBusyStatus.BUSY, FreeBusyStatus.TENTATIVE])
def test_a_written_busy_block_reads_back(status: FreeBusyStatus) -> None:
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, tzinfo=UTC)
    written_at = datetime(2026, 9, 30, 12, tzinfo=UTC)
    planning = CalendarEvent(
        source=EventSource(system=NEXTCLOUD, original_id="planning"),
        interval=DatetimeInterval(start=t0, end=t1),
        free_busy_status=status,
    )
    busy_block = render_busy_block(planning, written_at)

    calendar = Calendar.from_ical(busy_block.to_ical())

    assert read_busy_block(calendar) == BusyPeriod(start=t0, end=t1, status=status)
    assert read_busy_block_timestamp(calendar) == written_at


def test_the_same_update_gives_the_same_bytes() -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, tzinfo=UTC)
    planning = CalendarEvent(
        source=EventSource(system=NEXTCLOUD, original_id="planning"),
        interval=DatetimeInterval(start=t0, end=t1),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    first = render_busy_block(planning, written_at)
    second = render_busy_block(planning, written_at)

    assert first.to_ical() == second.to_ical()


def test_an_event_with_a_date_is_not_a_busy_block() -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    t0 = date(2026, 10, 1)
    t1 = date(2026, 10, 2)
    all_day = make_event(start=t0, end=t1, written_at=written_at)

    assert read_busy_block(make_calendar(all_day)) is None


def test_the_name_is_safe_and_the_same_for_the_same_source() -> None:
    occurrence = EventSource(system=NEXTCLOUD, original_id="a/b+c=" * 100)

    name = make_busy_block_name(occurrence)

    assert name == make_busy_block_name(occurrence)
    planning = EventSource(system=MICROSOFT_CLOUD, original_id="planning")
    assert name != make_busy_block_name(planning)
    assert name.startswith("msync-busy-") and name.endswith(".ics")
    assert len(name) == len("msync-busy-") + 32 + len(".ics")
    assert "/" not in name


def test_a_busy_block_is_named_by_its_uid_and_shows_nothing_private() -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    t0 = datetime(2026, 10, 1, 9, tzinfo=UTC)
    t1 = datetime(2026, 10, 1, 10, tzinfo=UTC)
    planning = CalendarEvent(
        source=EventSource(
            system=NEXTCLOUD, original_id="weekly-planning/20261001T090000Z"
        ),
        interval=DatetimeInterval(start=t0, end=t1),
        free_busy_status=FreeBusyStatus.BUSY,
    )

    busy_event = one(render_busy_block(planning, written_at).events)

    name = make_busy_block_name(
        EventSource(system=NEXTCLOUD, original_id="weekly-planning/20261001T090000Z")
    )
    assert f"{busy_event['UID']}.ics" == name
    assert busy_event["SUMMARY"] == "Busy"
    assert set(cast(Iterable[str], busy_event)) == {
        "UID",
        "DTSTAMP",
        "DTSTART",
        "DTEND",
        "SUMMARY",
        "TRANSP",
        "STATUS",
    }


@pytest.mark.parametrize(
    ("status", "transparency", "ical_status"),
    [
        (FreeBusyStatus.BUSY, "OPAQUE", "CONFIRMED"),
        (FreeBusyStatus.TENTATIVE, "OPAQUE", "TENTATIVE"),
        (FreeBusyStatus.FREE, "TRANSPARENT", "CONFIRMED"),
        # An unknown event is shown as busy.
        (FreeBusyStatus.UNKNOWN, "OPAQUE", "CONFIRMED"),
    ],
)
def test_the_status_of_an_event_sets_how_its_busy_block_shows(
    status: FreeBusyStatus,
    transparency: str,
    ical_status: str,
) -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    t0 = datetime(2026, 10, 1, 9, tzinfo=UTC)
    t1 = datetime(2026, 10, 1, 10, tzinfo=UTC)
    planning = CalendarEvent(
        source=EventSource(system=NEXTCLOUD, original_id="planning"),
        interval=DatetimeInterval(start=t0, end=t1),
        free_busy_status=status,
    )

    busy_event = one(render_busy_block(planning, written_at).events)

    assert busy_event["TRANSP"] == transparency
    assert busy_event["STATUS"] == ical_status


def test_a_full_day_is_written_as_dates() -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    day_off = CalendarEvent(
        source=EventSource(system=NEXTCLOUD, original_id="day-off"),
        interval=FullDayInterval(start=date(2026, 10, 1), end=date(2026, 10, 2)),
        free_busy_status=FreeBusyStatus.BUSY,
    )

    busy_block = Calendar.from_ical(render_busy_block(day_off, written_at).to_ical())

    busy_event = one(busy_block.events)
    assert not isinstance(busy_event.start, datetime)
    assert busy_event.start == date(2026, 10, 1)
    assert busy_event.end == date(2026, 10, 2)


@pytest.mark.parametrize(
    "no_time",
    [
        DatetimeInterval(
            start=datetime(2026, 10, 1, 9, tzinfo=UTC),
            end=datetime(2026, 10, 1, 9, tzinfo=UTC),
        ),
        FullDayInterval(start=date(2026, 10, 1), end=date(2026, 10, 1)),
    ],
)
def test_an_event_that_takes_no_time_is_written_transparent_without_an_end(
    no_time: Interval,
) -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    reminder = CalendarEvent(
        source=EventSource(system=NEXTCLOUD, original_id="reminder"),
        interval=no_time,
        free_busy_status=FreeBusyStatus.BUSY,
    )

    busy_event = one(render_busy_block(reminder, written_at).events)

    assert "DTEND" not in busy_event
    assert busy_event["TRANSP"] == "TRANSPARENT"


def test_an_event_that_ends_before_it_starts_is_written_free_and_logged() -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    t0 = datetime(2026, 10, 1, 9, tzinfo=UTC)
    t1 = datetime(2026, 10, 1, 10, tzinfo=UTC)
    backwards = CalendarEvent(
        source=EventSource(system=NEXTCLOUD, original_id="backwards"),
        interval=DatetimeInterval(start=t1, end=t0),
        free_busy_status=FreeBusyStatus.BUSY,
    )

    with capture_logs() as logs:
        busy_event = one(render_busy_block(backwards, written_at).events)

    assert busy_event["TRANSP"] == "TRANSPARENT"
    assert {
        "event": "event ends before it starts",
        "original_id": "backwards",
        "log_level": "warning",
    } in logs


def test_a_free_busy_request_asks_for_one_address_in_utc() -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    t0 = datetime(2026, 10, 1, tzinfo=DENMARK)
    t1 = datetime(2026, 10, 2, tzinfo=DENMARK)
    window = Span(start=t0, end=t1)

    request = render_free_busy_request(
        "/remote.php/dav/principals/users/msync/",
        "anne@example.com",
        window,
        written_at,
    )

    calendar = Calendar.from_ical(request.to_ical())
    assert calendar["METHOD"] == "REQUEST"
    free_busy = one(calendar.freebusy)
    assert free_busy["ORGANIZER"] == "/remote.php/dav/principals/users/msync/"
    assert free_busy["ATTENDEE"] == "mailto:anne@example.com"
    start, end = free_busy["DTSTART"], free_busy["DTEND"]
    assert isinstance(start, vDDDTypes) and isinstance(end, vDDDTypes)
    # Midnight in Denmark is 22:00 on the previous day in UTC, marked by the Z.
    assert start.to_ical() == b"20260930T220000Z"
    assert end.to_ical() == b"20261001T220000Z"


def test_busy_and_tentative_periods_are_read_and_the_rest_skipped() -> None:
    t0 = datetime(2026, 10, 1, tzinfo=DENMARK)
    t1 = datetime(2026, 10, 2, tzinfo=DENMARK)
    t2 = datetime(2026, 10, 3, tzinfo=UTC)
    half_hour = timedelta(minutes=30)
    free_busy = FreeBusy()
    free_busy.add("freebusy", (t0, t1))  # pyright: ignore[reportUnknownMemberType]
    free_busy.add("freebusy", (t2, half_hour), {"FBTYPE": "BUSY-TENTATIVE"})  # pyright: ignore[reportUnknownMemberType]
    # Parameter values are case-insensitive (RFC 5545 2).
    free_busy.add("freebusy", (t1, t2), {"FBTYPE": "busy-unavailable"})  # pyright: ignore[reportUnknownMemberType]
    free_busy.add("freebusy", (t1, t2), {"FBTYPE": "FREE"})  # pyright: ignore[reportUnknownMemberType]
    free_busy_answer = Calendar()
    free_busy_answer.add_component(free_busy)

    assert read_busy_periods(free_busy_answer) == [
        BusyPeriod(start=t0, end=t1),
        BusyPeriod(start=t2, end=t2 + half_hour, status=FreeBusyStatus.TENTATIVE),
    ]


def test_an_unknown_free_busy_type_is_read_as_busy() -> None:
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, tzinfo=UTC)
    free_busy = FreeBusy()
    free_busy.add("freebusy", (t0, t1), {"FBTYPE": "X-OUT-OF-OFFICE"})  # pyright: ignore[reportUnknownMemberType]
    free_busy_answer = Calendar()
    free_busy_answer.add_component(free_busy)

    assert read_busy_periods(free_busy_answer) == [BusyPeriod(start=t0, end=t1)]


def test_a_period_without_a_timezone_is_skipped() -> None:
    # RFC 5545 requires UTC, so a period without a timezone is broken data.
    t0 = datetime(2026, 10, 1, tzinfo=None)  # noqa: DTZ001
    t1 = datetime(2026, 10, 2, tzinfo=None)  # noqa: DTZ001
    t2 = datetime(2026, 10, 3, tzinfo=UTC)
    t3 = datetime(2026, 10, 4, tzinfo=UTC)
    free_busy = FreeBusy()
    free_busy.add("freebusy", (t0, t1))  # pyright: ignore[reportUnknownMemberType]
    free_busy.add("freebusy", (t2, t3))  # pyright: ignore[reportUnknownMemberType]
    free_busy_answer = Calendar()
    free_busy_answer.add_component(free_busy)

    assert read_busy_periods(free_busy_answer) == [BusyPeriod(start=t2, end=t3)]


def test_a_free_busy_answer_without_periods_has_no_busy_time() -> None:
    free_busy_answer = Calendar()
    free_busy_answer.add_component(FreeBusy())

    assert read_busy_periods(free_busy_answer) == []
