# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from datetime import UTC, date, datetime, timedelta

import pytest
from icalendar import Calendar, Event
from icalendar.enums import STATUS, TRANSP
from more_itertools import one

from sync.model import (
    MICROSOFT_CLOUD,
    NEXTCLOUD,
    CalendarEvent,
    DatetimeInterval,
    FreeBusyStatus,
    FullDayInterval,
    UpdateSource,
)
from sync.nextcloud.ical import (
    MAX_OCCURRENCES,
    TooManyOccurrencesError,
    make_busy_block_name,
    read_busy_block,
    read_busy_block_timestamp,
    read_events,
    render_busy_block,
    strip_to_timing,
)
from sync.nextcloud.spans import BusyPeriod
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
            event.original_id,
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
    busy_block = render_busy_block(
        "msync-busy-planning.ics", t0, t1, status, written_at
    )

    calendar = Calendar.from_ical(busy_block.to_ical())

    assert read_busy_block(calendar) == BusyPeriod(start=t0, end=t1, status=status)
    assert read_busy_block_timestamp(calendar) == written_at


def test_the_same_update_gives_the_same_bytes() -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, tzinfo=UTC)
    first = render_busy_block(
        "msync-busy-planning.ics", t0, t1, FreeBusyStatus.BUSY, written_at
    )
    second = render_busy_block(
        "msync-busy-planning.ics", t0, t1, FreeBusyStatus.BUSY, written_at
    )

    assert first.to_ical() == second.to_ical()


def test_an_event_with_a_date_is_not_a_busy_block() -> None:
    written_at = datetime(2026, 9, 1, tzinfo=UTC)
    t0 = date(2026, 10, 1)
    t1 = date(2026, 10, 2)
    all_day = make_event(start=t0, end=t1, written_at=written_at)

    assert read_busy_block(make_calendar(all_day)) is None


def test_the_name_is_safe_and_the_same_for_the_same_source() -> None:
    occurrence = UpdateSource(system=NEXTCLOUD, original_id="a/b+c=" * 100)

    name = make_busy_block_name(occurrence)

    assert name == make_busy_block_name(occurrence)
    planning = UpdateSource(system=MICROSOFT_CLOUD, original_id="planning")
    assert name != make_busy_block_name(planning)
    assert name.startswith("msync-busy-") and name.endswith(".ics")
    assert len(name) == len("msync-busy-") + 32 + len(".ics")
    assert "/" not in name
