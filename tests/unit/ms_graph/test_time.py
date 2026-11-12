# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from datetime import UTC, date, datetime

import pytest
from msgraph.generated.models.date_time_time_zone import (  # pyright: ignore[reportMissingTypeStubs]
    DateTimeTimeZone,
)

from sync.model import DatetimeInterval, FullDayInterval
from sync.ms_graph.time import (
    GraphInterval,
    MissingDatetimeError,
    MissingTimezoneError,
    UnknownTimezoneError,
    datetime_to_graph_datetime_timezone,
    graph_datetime_timezone_to_datetime,
    graph_interval_to_interval,
    interval_to_graph_interval,
)


def test_graph_datetime_timezone_missing_date_time_raises() -> None:
    with pytest.raises(MissingDatetimeError):
        _ = graph_datetime_timezone_to_datetime(
            DateTimeTimeZone(date_time=None, time_zone="UTC")
        )


def test_graph_datetime_timezone_missing_time_zone_raises() -> None:
    with pytest.raises(MissingTimezoneError):
        _ = graph_datetime_timezone_to_datetime(
            DateTimeTimeZone(date_time="2026-11-03T14:30:00", time_zone=None)
        )


def test_graph_datetime_timezone_not_utc_raises() -> None:
    with pytest.raises(UnknownTimezoneError):
        _ = graph_datetime_timezone_to_datetime(
            DateTimeTimeZone(
                date_time="2026-11-03T14:30:00",
                time_zone="Romance Standard Time",
            )
        )


def test_datetime_to_graph_datetime_timezone() -> None:
    dt = datetime(2026, 2, 9, tzinfo=UTC)

    dt_tz = datetime_to_graph_datetime_timezone(dt)

    assert dt_tz.date_time == "2026-02-09T00:00:00"
    assert dt_tz.time_zone == "UTC"


def test_graph_datetime_timezone_to_datetime() -> None:
    dt_tz = DateTimeTimeZone(
        date_time="2026-02-09T00:00:00",
        time_zone="UTC",
    )

    dt = graph_datetime_timezone_to_datetime(dt_tz)

    assert dt == datetime(2026, 2, 9, tzinfo=UTC)
    assert dt.tzinfo == UTC


def test_graph_datetime_timezone_to_datetime_microsoft_tz_name() -> None:
    """Graph also uses the Windows timezone name for UTC in some contexts."""

    dt_tz = DateTimeTimeZone(
        date_time="2026-02-09T00:00:00",
        time_zone="tzone://Microsoft/Utc",
    )

    dt = graph_datetime_timezone_to_datetime(dt_tz)

    assert dt == datetime(2026, 2, 9, tzinfo=UTC)
    assert dt.tzinfo == UTC


def test_datetime_interval_to_graph_interval() -> None:
    t0 = datetime(2020, 1, 1, hour=1, tzinfo=UTC)
    t1 = datetime(2020, 1, 1, hour=2, tzinfo=UTC)
    interval = DatetimeInterval(start=t0, end=t1)

    graph_interval = interval_to_graph_interval(interval)

    assert not graph_interval.is_all_day
    assert graph_interval.start.date_time == "2020-01-01T01:00:00"
    assert graph_interval.start.time_zone == "UTC"
    assert graph_interval.end.date_time == "2020-01-01T02:00:00"
    assert graph_interval.end.time_zone == "UTC"


def test_graph_interval_to_datetime_interval() -> None:
    t0 = DateTimeTimeZone(
        date_time="2026-01-01T01:00:00",
        time_zone="UTC",
    )
    t1 = DateTimeTimeZone(
        date_time="2026-01-01T02:00:00",
        time_zone="UTC",
    )
    graph_interval = GraphInterval(start=t0, end=t1, is_all_day=False)

    interval = graph_interval_to_interval(graph_interval)

    assert isinstance(interval, DatetimeInterval)
    assert interval.start == datetime(2026, 1, 1, hour=1, tzinfo=UTC)
    assert interval.start.tzinfo == UTC
    assert interval.end == datetime(2026, 1, 1, hour=2, tzinfo=UTC)
    assert interval.end.tzinfo == UTC


def test_full_day_interval_to_graph_interval() -> None:
    t0 = date(2020, 1, 1)
    t1 = date(2020, 1, 2)
    interval = FullDayInterval(start=t0, end=t1)

    graph_interval = interval_to_graph_interval(interval)

    assert graph_interval.is_all_day

    # MS Graph uses midnight by convention for all-day events
    assert graph_interval.start.date_time == "2020-01-01T00:00:00"
    assert graph_interval.start.time_zone == "UTC"
    assert graph_interval.end.date_time == "2020-01-02T00:00:00"
    assert graph_interval.end.time_zone == "UTC"


def test_graph_interval_to_full_day_interval() -> None:
    t0 = DateTimeTimeZone(
        date_time="2026-01-01T00:00:00",
        time_zone="UTC",
    )
    t1 = DateTimeTimeZone(
        date_time="2026-01-02T00:00:00",
        time_zone="UTC",
    )
    graph_interval = GraphInterval(start=t0, end=t1, is_all_day=True)

    interval = graph_interval_to_interval(graph_interval)

    assert isinstance(interval, FullDayInterval)
    assert interval.start == date(2026, 1, 1)
    assert interval.end == date(2026, 1, 2)
