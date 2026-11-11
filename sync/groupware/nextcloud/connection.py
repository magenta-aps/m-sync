# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

"""Nextcloud's errors and the HTTP connection the OCS and DAV clients share."""

from collections.abc import Mapping
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import quote

import httpx

_REQUEST_TIMEOUT_SECONDS = 30

# Apache closes idle connections after 5 s. Reusing one it closed fails.
_KEEPALIVE_LIMITS = httpx.Limits(keepalive_expiry=4.0)


class NextcloudError(Exception):
    pass


class RateLimitedError(NextcloudError):
    pass


class NotFoundError(NextcloudError):
    pass


class CalendarMissingError(NextcloudError):
    pass


class NotEditableError(NextcloudError):
    pass


class UnknownAddressError(NextcloudError):
    pass


class UserExistsError(NextcloudError):
    pass


def make_http_client(
    url: str,
    auth: httpx.Auth,
    keep_cookies: bool = True,
) -> httpx.AsyncClient:
    # A cookie jar that refuses all, so Nextcloud keeps no session for the login.
    cookies = (
        None if keep_cookies else CookieJar(DefaultCookiePolicy(allowed_domains=[]))
    )
    return httpx.AsyncClient(
        base_url=url.rstrip("/"),
        auth=auth,
        timeout=_REQUEST_TIMEOUT_SECONDS,
        limits=_KEEPALIVE_LIMITS,
        # Marks us as an API client, so Nextcloud skips the password login on
        # every POST.
        headers={"OCS-APIRequest": "true"},
        cookies=cookies,
    )


async def send_request(
    http_client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, str | int] | None = None,
    content: str | bytes | None = None,
    data: Mapping[str, str] | None = None,
) -> httpx.Response:
    try:
        return await http_client.request(
            method, path, headers=headers, params=params, content=content, data=data
        )
    except httpx.HTTPError as error:
        # From None: the frames below hold passwords as locals.
        raise NextcloudError(f"{method} {path}: {type(error).__name__}") from None


def quote_path_segment(segment: str) -> str:
    # Ids are opaque to us. A `/` or `?` in one must not change which resource
    # we hit.
    return quote(segment, safe="")
