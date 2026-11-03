# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from sync.model import Interval


@pytest.mark.xfail(raises=ValidationError, reason="bug reproduction")
def test_interval_validation():
    _ = Interval(
        full_day=False,
        start=datetime(year=2020, month=1, day=1, tzinfo=UTC),
        end=datetime(year=2020, month=1, day=2, tzinfo=ZoneInfo("Europe/Copenhagen")),
    )


def test_interval_validation_fails_on_naive_start_time():
    with pytest.raises(ValidationError):
        _ = Interval(
            full_day=False,
            start=datetime(year=2020, month=1, day=1, tzinfo=None),  # noqa: DTZ001
            end=datetime(year=2020, month=1, day=2, tzinfo=UTC),
        )


def test_interval_validation_fails_on_naive_end_time():
    with pytest.raises(ValidationError):
        _ = Interval(
            full_day=False,
            start=datetime(year=2020, month=1, day=1, tzinfo=UTC),
            end=datetime(year=2020, month=1, day=2, tzinfo=None),  # noqa: DTZ001
        )
