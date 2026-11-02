# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from msgraph.generated.models.date_time_time_zone import (  # pyright: ignore[reportMissingTypeStubs]
    DateTimeTimeZone,
)

from ..model import DatetimeInterval, FullDayInterval, Interval

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


@dataclass(frozen=True)
class GraphInterval:
    start: DateTimeTimeZone
    end: DateTimeTimeZone
    is_all_day: bool = False


def interval_to_graph_interval(
    interval: Interval,
) -> GraphInterval:
    match interval:
        case DatetimeInterval(start=start, end=end):
            graph_start = datetime_to_graph_datetime_timezone(start)
            graph_end = datetime_to_graph_datetime_timezone(end)
            return GraphInterval(
                start=graph_start,
                end=graph_end,
            )
        case FullDayInterval() as full_day:
            # graph's full-day semantics are weird, timestamps should always
            # be in UTC, even though the timezone is ignored for full-day events
            # see https://github.com/microsoftgraph/msgraph-sdk-javascript/issues/1158#issuecomment-1411038157
            start = full_day.start_datetime().replace(tzinfo=UTC)
            graph_start = datetime_to_graph_datetime_timezone(start)
            end = full_day.end_datetime().replace(tzinfo=UTC)
            graph_end = datetime_to_graph_datetime_timezone(end)
            return GraphInterval(
                start=graph_start,
                end=graph_end,
                is_all_day=True,
            )
        case _:
            assert False, "unreachable"


def graph_interval_to_interval(interval: GraphInterval) -> Interval:
    start = graph_datetime_timezone_to_datetime(interval.start)
    end = graph_datetime_timezone_to_datetime(interval.end)
    if interval.is_all_day:
        return FullDayInterval(start=start.date(), end=end.date())
    else:
        return DatetimeInterval(start=start, end=end)
