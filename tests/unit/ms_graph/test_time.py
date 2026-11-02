# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from datetime import UTC, datetime

import pytest
from msgraph.generated.models.date_time_time_zone import (  # pyright: ignore[reportMissingTypeStubs]
    DateTimeTimeZone,
)

from sync.ms_graph.time import (
    MissingDatetimeError,
    MissingTimezoneError,
    UnknownTimezoneError,
    datetime_to_graph_datetime_timezone,
    graph_datetime_timezone_to_datetime,
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
    dt_tz = DateTimeTimeZone(date_time="2026-02-09T00:00:00", time_zone="UTC")

    dt = graph_datetime_timezone_to_datetime(dt_tz)

    assert dt == datetime(2026, 2, 9, tzinfo=UTC)
    assert dt.tzinfo == UTC
