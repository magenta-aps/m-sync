# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from sync.model import DatetimeInterval, FullDayInterval

_CPH = ZoneInfo("Europe/Copenhagen")


def test_interval_validation():
    _ = DatetimeInterval(
        start=datetime(year=2020, month=1, day=1, tzinfo=UTC),
        end=datetime(year=2020, month=1, day=2, tzinfo=_CPH),
    )


def test_interval_validation_fails_on_naive_start_time():
    with pytest.raises(ValidationError):
        _ = DatetimeInterval(
            start=datetime(year=2020, month=1, day=1, tzinfo=None),  # noqa: DTZ001
            end=datetime(year=2020, month=1, day=2, tzinfo=UTC),
        )


def test_interval_validation_fails_on_naive_end_time():
    with pytest.raises(ValidationError):
        _ = DatetimeInterval(
            start=datetime(year=2020, month=1, day=1, tzinfo=UTC),
            end=datetime(year=2020, month=1, day=2, tzinfo=None),  # noqa: DTZ001
        )


def test_full_day_interval_timezones():
    interval = FullDayInterval(
        start=date(2020, 1, 1),
        end=date(2020, 1, 2),
    )

    # full-day intervals canonicalize to midnight in UTC
    assert interval.start_datetime() == datetime(
        year=2020,
        month=1,
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
        tzinfo=UTC,
    )
    assert interval.end_datetime() == datetime(
        year=2020,
        month=1,
        day=2,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
        tzinfo=UTC,
    )
