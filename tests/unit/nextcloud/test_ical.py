# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from datetime import UTC, date, datetime, timedelta

import pytest
from icalendar import Calendar, Event
from icalendar.enums import STATUS, TRANSP
from more_itertools import one

from sync.model import CalendarEvent, DatetimeInterval, FullDayInterval
from sync.nextcloud.ical import (
    MAX_OCCURRENCES,
    TooManyOccurrencesError,
    read_events,
    strip_to_timing,
)
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
    # 09:00 Danish summer time on 1 October is 07:00 UTC.
    floating = make_event(
        start=datetime.fromisoformat("2026-10-01T09:00:00"),
        end=datetime.fromisoformat("2026-10-01T10:00:00"),
    )

    event = one(read_in_window(make_calendar(floating)))

    assert isinstance(event.interval, DatetimeInterval)
    assert event.interval.start == datetime(2026, 10, 1, 7, 0, tzinfo=UTC)


def test_an_event_starting_at_a_clock_time_and_ending_on_a_date_is_rejected() -> None:
    # Event.new refuses this, but other clients write it.
    time_and_whole_date = make_event()
    del time_and_whole_date["DTEND"]
    time_and_whole_date.add("dtend", date(2026, 10, 2))  # pyright: ignore[reportUnknownMemberType]

    with pytest.raises(ValueError, match="same type"):
        _ = read_in_window(make_calendar(time_and_whole_date))


def test_an_all_day_event_is_a_full_day() -> None:
    all_day = make_event(start=date(2026, 10, 1), end=date(2026, 10, 2))

    event = one(read_in_window(make_calendar(all_day)))

    assert isinstance(event.interval, FullDayInterval)
    assert event.interval.start == date(2026, 10, 1)
    assert event.interval.end == date(2026, 10, 2)


def test_read_events_applies_moved_and_cancelled_occurrences() -> None:
    standup = make_series("daily-standup", frequency="daily", count=3)
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
    other_meeting = Event.new(
        uid="other-meeting",
        recurrence_id=datetime(2026, 10, 8, 9, tzinfo=UTC),
        start=datetime(2026, 10, 8, 9, tzinfo=UTC),
        end=datetime(2026, 10, 8, 10, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="different UIDs"):
        _ = read_in_window(make_calendar(make_series(), other_meeting))


def test_a_series_is_cut_at_the_limit() -> None:
    every_minute = make_series("every-minute", frequency="minutely", count=None)

    with pytest.raises(TooManyOccurrencesError) as raised:
        _ = read_in_window(make_calendar(every_minute))

    assert len(raised.value.first_occurrences) == MAX_OCCURRENCES


@pytest.mark.parametrize("count", [MAX_OCCURRENCES - 1, MAX_OCCURRENCES])
def test_a_series_up_to_the_limit_is_read_whole(count: int) -> None:
    hourly = make_series("hourly-series", frequency="hourly", count=count)

    assert len(read_in_window(make_calendar(hourly))) == count


def test_one_more_than_the_limit_is_cut() -> None:
    hourly = make_series("hourly-series", "hourly", count=MAX_OCCURRENCES + 1)

    with pytest.raises(TooManyOccurrencesError) as raised:
        _ = read_in_window(make_calendar(hourly))

    assert len(raised.value.first_occurrences) == MAX_OCCURRENCES


def test_timing_only_keeps_what_expanding_needs_and_nothing_private() -> None:
    series = make_series()
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
    calendar = make_calendar(make_series())

    from_text = Calendar.from_ical(make_ics(make_series()))

    assert read_in_window(from_text) == read_in_window(calendar)
