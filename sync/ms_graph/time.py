# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from datetime import UTC, datetime

import structlog
from msgraph.generated.models.date_time_time_zone import (  # pyright: ignore[reportMissingTypeStubs]
    DateTimeTimeZone,
)

logger = structlog.stdlib.get_logger()


class MissingDatetimeError(Exception):
    pass


class MissingTimezoneError(Exception):
    pass


class UnknownTimezoneError(Exception):
    pass


def graph_datetime_timezone_to_datetime(
    dt_tz: DateTimeTimeZone,
) -> datetime:
    """Converts from a Microsoft Graph `DateTimeTimeZone` to a Python `datetime`.

    `DateTimeTimeZone` is made up of a naive `date_time` and a timezone name. We
    only support UTC timezones, since Microsoft Graph always uses UTC, as long
    as no `Prefer: outlook.timezone` is set in Graph queries.
    """

    if dt_tz.date_time is None:
        raise MissingDatetimeError()

    if dt_tz.time_zone is None:
        raise MissingTimezoneError()

    if dt_tz.time_zone != "UTC":
        raise UnknownTimezoneError(
            f"timezones other than UTC are not supported: tz={dt_tz.time_zone}"
        )

    dt = datetime.fromisoformat(dt_tz.date_time)

    if dt.tzinfo is not None:
        logger.warning(
            "expected date_time to be naive, but has a timezone", tz=dt.tzinfo
        )

    return dt.replace(tzinfo=UTC)


def datetime_to_graph_datetime_timezone(dt: datetime) -> DateTimeTimeZone:
    assert dt.tzinfo is not None

    return DateTimeTimeZone(
        date_time=dt.replace(tzinfo=None).isoformat(),
        time_zone="UTC",
    )
