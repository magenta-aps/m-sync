# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

"""What Nextcloud knows about a calendar user, asked by the service account."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import final, override
from xml.etree import ElementTree

from icalendar import Calendar

from ...model import DatetimeInterval
from ...nextcloud.ical import read_busy_periods, render_free_busy_request
from ...nextcloud.spans import BusyPeriod, Span
from .connection import (
    NextcloudError,
    UnknownAddressError,
    quote_path_segment,
)
from .dav import (
    CALDAV_NAMESPACE,
    DAV_ROOT,
    XML_CONTENT_TYPE,
    DavClient,
    check_dav_status,
    parse_xml,
)

_PROPFIND_ADDRESS_BODY = (
    '<?xml version="1.0"?>'
    '<dav:propfind xmlns:dav="DAV:" xmlns:caldav="urn:ietf:params:xml:ns:caldav">'
    "<dav:prop><caldav:calendar-user-address-set/></dav:prop></dav:propfind>"
)


class NextcloudPrincipals(ABC):
    @abstractmethod
    async def user_exists(self, user_id: str) -> bool:
        """Whether the user still has a principal. A deleted user has none."""

    @abstractmethod
    async def get_busy_intervals(
        self, address: str, start: datetime, end: datetime
    ) -> list[DatetimeInterval] | None:
        """Busy time from Nextcloud's scheduling. None if Nextcloud gives none.
        Raises UnknownAddressError if no calendar user has the address."""

    @abstractmethod
    async def get_busy_periods_by_type(
        self, address: str, start: datetime, end: datetime
    ) -> list[BusyPeriod] | None:
        """The busy periods from Nextcloud's scheduling with each period's status."""


@final
class RealNextcloudPrincipals(NextcloudPrincipals):
    _service_dav_client: DavClient
    _service_user_id: str

    def __init__(self, service_dav_client: DavClient, service_user_id: str) -> None:
        self._service_dav_client = service_dav_client
        self._service_user_id = service_user_id

    @override
    async def user_exists(self, user_id: str) -> bool:
        # Asked over DAV by the service account. A sub-admin gets "not found"
        # for every user outside its groups.
        response = await self._service_dav_client.request(
            "PROPFIND",
            f"{DAV_ROOT}principals/users/{quote_path_segment(user_id)}/",
            _PROPFIND_ADDRESS_BODY,
            headers={"Depth": "0", "Content-Type": XML_CONTENT_TYPE},
        )
        if response.status_code == 404:
            return False
        check_dav_status(response)
        return True

    @override
    async def get_busy_intervals(
        self, address: str, start: datetime, end: datetime
    ) -> list[DatetimeInterval] | None:
        periods = await self.get_busy_periods_by_type(address, start, end)
        if periods is None:
            return None
        return [
            DatetimeInterval(start=period.start, end=period.end) for period in periods
        ]

    @override
    async def get_busy_periods_by_type(
        self, address: str, start: datetime, end: datetime
    ) -> list[BusyPeriod] | None:
        service_user_id = quote_path_segment(self._service_user_id)
        # The service account has no address, so it asks as its principal.
        free_busy_request = render_free_busy_request(
            f"{DAV_ROOT}principals/users/{service_user_id}/",
            address,
            Span(start=start, end=end),
            timestamp=datetime.now(UTC),
        )
        response = await self._service_dav_client.request(
            "POST",
            f"{DAV_ROOT}calendars/{service_user_id}/outbox/",
            free_busy_request.to_ical(),
            headers={"Content-Type": "text/calendar; charset=utf-8"},
        )
        check_dav_status(response)
        return read_schedule_response(parse_xml(response))


def read_schedule_response(
    schedule_response: ElementTree.Element,
) -> list[BusyPeriod] | None:
    """The busy time for the one address we asked about. None if the answer has
    none. Raises UnknownAddressError if no calendar user has the address."""
    recipient_response = schedule_response.find(f"{CALDAV_NAMESPACE}response")
    if recipient_response is None:
        return None
    request_status = recipient_response.findtext(
        f"{CALDAV_NAMESPACE}request-status", default=""
    )
    if request_status.startswith("3.7"):
        raise UnknownAddressError(f"free/busy: {request_status}")
    # E.g. 3.8 (no authority) or 5.3 (no scheduling): unknown, not free.
    if not request_status.startswith("2."):
        return None
    calendar_data = recipient_response.findtext(
        f"{CALDAV_NAMESPACE}calendar-data", default=""
    )
    try:
        calendar = Calendar.from_ical(calendar_data)
    except ValueError:
        # Without the value, which is someone's busy time.
        raise NextcloudError("free/busy: unreadable calendar data") from None
    return read_busy_periods(calendar)
