# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

"""Ghost calendars over CalDAV: set up by the ghost, written by the service account."""

import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Literal, final, override
from urllib.parse import unquote
from xml.sax.saxutils import escape

import httpx
from icalendar import Calendar
from pydantic import SecretStr

from .connection import (
    CalendarMissingError,
    NextcloudError,
    quote_path_segment,
)
from .dav import (
    CALDAV_NAMESPACE,
    DAV_NAMESPACE,
    DAV_ROOT,
    XML_CONTENT_TYPE,
    DavClient,
    check_dav_status,
    parse_xml,
)

# The calendar in each ghost that holds our busy blocks.
_GHOST_CALENDAR = "msync"

# A share is Nextcloud's calendar sharing. Each ghost shares its calendar with
# the service account, which then finds it in its own calendar home as
# `msync_shared_by_<owner>`. The busy blocks are written there.
_SHARED_BY = "_shared_by_"

# The end of the href of a ghost calendar shared with the service account.
_GHOST_CALENDAR_HREF = re.compile(rf"/{_GHOST_CALENDAR}{_SHARED_BY}(?P<owner>[^/]+)/?$")

_PROPFIND_DISPLAY_NAME_BODY = (
    '<?xml version="1.0"?>'
    '<dav:propfind xmlns:dav="DAV:">'
    "<dav:prop><dav:displayname/></dav:prop></dav:propfind>"
)

_PROPFIND_PRIVILEGES_BODY = (
    '<?xml version="1.0"?>'
    '<dav:propfind xmlns:dav="DAV:">'
    "<dav:prop><dav:current-user-privilege-set/></dav:prop>"
    "</dav:propfind>"
)

_MAKE_CALENDAR_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<caldav:mkcalendar xmlns:dav="DAV:" xmlns:caldav="urn:ietf:params:xml:ns:caldav">'
    "<dav:set><dav:prop><dav:displayname>msync</dav:displayname>"
    "<caldav:supported-calendar-component-set>"
    '<caldav:comp name="VEVENT"/>'
    "</caldav:supported-calendar-component-set>"
    "</dav:prop></dav:set></caldav:mkcalendar>"
)


class NextcloudCalendars(ABC):
    @abstractmethod
    async def prepare_ghost_calendar(
        self, owner: str, owner_password: SecretStr
    ) -> str:
        """As the ghost, give it a calendar shared read-write with the service
        account and make `personal` transparent. Return the share's href. The
        caller generates the password per call and never stores it."""

    @abstractmethod
    async def release_ghost_calendar(
        self, owner: str, owner_password: SecretStr
    ) -> None:
        """Undo `prepare_ghost_calendar`, as the ghost. The caller generates the
        password per call and never stores it."""

    @abstractmethod
    async def list_ghost_calendar_hrefs(self) -> dict[str, str]:
        """Ghost calendars shared with the service account, by owner."""

    @abstractmethod
    async def put_object(
        self, calendar_href: str, object_name: str, calendar: Calendar
    ) -> None: ...

    @abstractmethod
    async def delete_object(self, calendar_href: str, object_name: str) -> None: ...

    @abstractmethod
    async def list_objects(
        self, calendar_href: str, start: datetime, end: datetime
    ) -> dict[str, Calendar]:
        """Objects overlapping start to end, by name."""


@final
class RealNextcloudCalendars(NextcloudCalendars):
    _service_dav_client: DavClient
    _url: str
    _service_user_id: str

    def __init__(
        self,
        service_dav_client: DavClient,
        url: str,
        service_user_id: str,
    ) -> None:
        self._service_dav_client = service_dav_client
        self._url = url
        self._service_user_id = service_user_id

    @override
    async def prepare_ghost_calendar(
        self, owner: str, owner_password: SecretStr
    ) -> str:
        calendar_home = _calendar_home_path(owner)
        ghost_calendar_href = f"{calendar_home}{_GHOST_CALENDAR}/"
        ghost_auth = httpx.BasicAuth(owner, owner_password.get_secret_value())
        # A client of its own without cookies: the ghost's session must not leak.
        async with DavClient(
            self._url, ghost_auth, keep_cookies=False
        ) as ghost_dav_client:
            response = await ghost_dav_client.request(
                "MKCALENDAR",
                ghost_calendar_href,
                _MAKE_CALENDAR_BODY,
                headers={"Content-Type": XML_CONTENT_TYPE},
            )
            # 405 means it exists already, from an earlier attempt.
            if response.status_code != 405:
                check_dav_status(response)
            # Invitations to the ghost's address land in `personal`. It is
            # transparent, so they never count as busy. It is unshared, so we
            # never see what they say.
            await _set_transparency(
                ghost_dav_client, f"{calendar_home}personal/", "transparent"
            )
            await _share_read_write(
                ghost_dav_client, ghost_calendar_href, self._service_user_id
            )
        return get_ghost_calendar_href(self._service_user_id, owner)

    @override
    async def release_ghost_calendar(
        self, owner: str, owner_password: SecretStr
    ) -> None:
        calendar_home = _calendar_home_path(owner)
        ghost_auth = httpx.BasicAuth(owner, owner_password.get_secret_value())
        async with DavClient(
            self._url, ghost_auth, keep_cookies=False
        ) as ghost_dav_client:
            await _set_transparency(
                ghost_dav_client, f"{calendar_home}personal/", "opaque"
            )
            # Skip the trash bin, where it would stay shared with us and busy.
            response = await ghost_dav_client.request(
                "DELETE",
                f"{calendar_home}{_GHOST_CALENDAR}/",
                headers={"X-NC-CalDAV-No-Trashbin": "1"},
            )
            if response.status_code != 404:
                check_dav_status(response)

    @override
    async def list_ghost_calendar_hrefs(self) -> dict[str, str]:
        response = await self._service_dav_client.request(
            "PROPFIND",
            _calendar_home_path(self._service_user_id),
            _PROPFIND_DISPLAY_NAME_BODY,
            headers={"Depth": "1", "Content-Type": XML_CONTENT_TYPE},
        )
        check_dav_status(response)
        calendar_hrefs: dict[str, str] = {}
        for element in parse_xml(response).iter(f"{DAV_NAMESPACE}href"):
            ghost_calendar = read_ghost_calendar_href(element.text or "")
            if ghost_calendar is not None:
                owner, calendar_href = ghost_calendar
                calendar_hrefs[owner] = calendar_href
        return calendar_hrefs

    @override
    async def put_object(
        self, calendar_href: str, object_name: str, calendar: Calendar
    ) -> None:
        response = await self._service_dav_client.request(
            "PUT",
            f"{calendar_href.rstrip('/')}/{object_name}",
            calendar.to_ical(),
            headers={"Content-Type": "text/calendar; charset=utf-8"},
        )
        if response.status_code == 404:
            raise CalendarMissingError(f"PUT {response.request.url.path}: HTTP 404")
        check_dav_status(response)

    @override
    async def delete_object(self, calendar_href: str, object_name: str) -> None:
        response = await self._service_dav_client.request(
            "DELETE", f"{calendar_href.rstrip('/')}/{object_name}"
        )
        if response.status_code == 404:
            await self._check_writable(
                calendar_href, f"DELETE {response.request.url.path}"
            )
            return
        check_dav_status(response)

    @override
    async def list_objects(
        self, calendar_href: str, start: datetime, end: datetime
    ) -> dict[str, Calendar]:
        response = await self._service_dav_client.request(
            "REPORT",
            f"{calendar_href.rstrip('/')}/",
            _make_calendar_query(start, end),
            headers={"Depth": "1", "Content-Type": XML_CONTENT_TYPE},
        )
        if response.status_code == 404:
            raise CalendarMissingError(f"REPORT {response.request.url.path}: HTTP 404")
        check_dav_status(response)
        objects: dict[str, Calendar] = {}
        for response_element in parse_xml(response).iter(f"{DAV_NAMESPACE}response"):
            href = (response_element.findtext(f"{DAV_NAMESPACE}href") or "").rstrip("/")
            object_name = unquote(href.rpartition("/")[2])
            calendar_data = response_element.findtext(
                f".//{CALDAV_NAMESPACE}calendar-data", default=""
            )
            if not object_name:
                continue
            try:
                objects[object_name] = Calendar.from_ical(calendar_data)
            except ValueError:
                # From None: the error quotes the calendar data.
                raise NextcloudError(
                    f"REPORT {response.request.url.path}: unreadable {object_name}"
                ) from None
        return objects

    async def _check_writable(
        self, calendar_href: str, request_description: str
    ) -> None:
        """Whether an object that answered 404 is really gone. A missing calendar
        and a read-only share also answer 404. Only the calendar's own answer
        tells."""
        response = await self._service_dav_client.request(
            "PROPFIND",
            f"{calendar_href.rstrip('/')}/",
            _PROPFIND_PRIVILEGES_BODY,
            headers={"Depth": "0", "Content-Type": XML_CONTENT_TYPE},
        )
        if response.status_code == 404:
            raise CalendarMissingError(f"{request_description}: HTTP 404")
        check_dav_status(response)
        # Deleting an object takes `unbind` on its calendar.
        if next(parse_xml(response).iter(f"{DAV_NAMESPACE}unbind"), None) is None:
            raise CalendarMissingError(f"{request_description}: read-only")


async def _set_transparency(
    ghost_dav_client: DavClient,
    calendar_href: str,
    transparency: Literal["transparent", "opaque"],
) -> None:
    transparency_body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<dav:propertyupdate xmlns:dav="DAV:" xmlns:caldav="urn:ietf:params:xml:ns:caldav">'
        "<dav:set><dav:prop><caldav:schedule-calendar-transp>"
        f"<caldav:{transparency}/></caldav:schedule-calendar-transp>"
        "</dav:prop></dav:set></dav:propertyupdate>"
    )
    response = await ghost_dav_client.request(
        "PROPPATCH",
        calendar_href,
        transparency_body,
        headers={"Content-Type": XML_CONTENT_TYPE},
    )
    check_dav_status(response)
    # A 207 can hide a property that was not set, since the status is per
    # property. Each PROPPATCH here sets one, so one `200 OK` means it was set.
    if response.status_code == 207 and "200 OK" not in response.text:
        raise NextcloudError(f"PROPPATCH {response.request.url.path}: property not set")


async def _share_read_write(
    ghost_dav_client: DavClient, calendar_href: str, receiver_user_id: str
) -> None:
    # Without `owncloud:read-write` the share is read-only and every write fails
    # with a bare 404.
    share_body = (
        '<owncloud:share xmlns:dav="DAV:" xmlns:owncloud="http://owncloud.org/ns">'
        "<owncloud:set>"
        f"<dav:href>principal:principals/users/{escape(receiver_user_id)}</dav:href>"
        "<owncloud:summary>msync</owncloud:summary><owncloud:read-write/>"
        "</owncloud:set></owncloud:share>"
    )
    response = await ghost_dav_client.request(
        "POST",
        calendar_href,
        share_body,
        headers={"Content-Type": XML_CONTENT_TYPE},
    )
    check_dav_status(response)


def read_ghost_calendar_href(href: str) -> tuple[str, str] | None:
    """The owner and href of a ghost calendar shared with the service account.
    None for any other calendar."""
    href_match = _GHOST_CALENDAR_HREF.search(href)
    if href_match is None:
        return None
    try:
        dav_root_start = href.index(DAV_ROOT)
    except ValueError:
        raise NextcloudError(f"shared calendar outside {DAV_ROOT}: {href}") from None
    # Relative to the DAV root, so a Nextcloud webroot is not doubled.
    return unquote(href_match["owner"]), href[dav_root_start:]


def get_ghost_calendar_href(service_user_id: str, owner: str) -> str:
    """The href of the owner's ghost calendar as shared with the service account.
    It makes no request, so the calendar may not exist."""
    shared_calendar = f"{_GHOST_CALENDAR}{_SHARED_BY}{quote_path_segment(owner)}"
    return f"{_calendar_home_path(service_user_id)}{shared_calendar}/"


def _calendar_home_path(user_id: str) -> str:
    return f"{DAV_ROOT}calendars/{quote_path_segment(user_id)}/"


def _make_calendar_query(start: datetime, end: datetime) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<caldav:calendar-query xmlns:dav="DAV:"'
        ' xmlns:caldav="urn:ietf:params:xml:ns:caldav">'
        "<dav:prop><caldav:calendar-data/></dav:prop>"
        "<caldav:filter>"
        '<caldav:comp-filter name="VCALENDAR"><caldav:comp-filter name="VEVENT">'
        f'<caldav:time-range start="{start.astimezone(UTC):%Y%m%dT%H%M%SZ}"'
        f' end="{end.astimezone(UTC):%Y%m%dT%H%M%SZ}"/>'
        "</caldav:comp-filter></caldav:comp-filter>"
        "</caldav:filter></caldav:calendar-query>"
    )
