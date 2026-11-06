# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

"""A plain WebDAV/CalDAV client: requests, status checks and XML answers."""

from collections.abc import Mapping
from typing import Self
from xml.etree import ElementTree

import httpx

from .connection import NextcloudError, make_http_client, send_request

DAV_ROOT = "/remote.php/dav/"
DAV_NAMESPACE = "{DAV:}"
CALDAV_NAMESPACE = "{urn:ietf:params:xml:ns:caldav}"
XML_CONTENT_TYPE = "application/xml; charset=utf-8"

# 207 only says the request was read. The caller checks what is inside it.
_ACCEPTED_STATUS_CODES = {200, 201, 204, 207}


class DavClient:
    _http_client: httpx.AsyncClient

    def __init__(
        self,
        url: str,
        auth: httpx.Auth,
        *,
        keep_cookies: bool = True,
    ) -> None:
        self._http_client = make_http_client(url, auth, keep_cookies)

    async def request(
        self,
        method: str,
        path: str,
        content: str | bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        return await send_request(
            self._http_client, method, path, headers=headers, content=content
        )

    async def close(self) -> None:
        await self._http_client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exception_info: object) -> None:
        await self.close()


def check_dav_status(response: httpx.Response) -> None:
    if response.status_code not in _ACCEPTED_STATUS_CODES:
        request = response.request
        raise NextcloudError(
            f"{request.method} {request.url.path}: HTTP {response.status_code}"
        )


def parse_xml(response: httpx.Response) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(response.content)
    except ElementTree.ParseError:
        request = response.request
        raise NextcloudError(
            f"{request.method} {request.url.path}: unreadable answer"
        ) from None
