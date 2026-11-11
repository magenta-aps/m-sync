# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import base64
import re
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, TypeAdapter

from sync.config import NextcloudSettings
from sync.groupware.nextcloud.calendars import get_ghost_calendar_href
from sync.groupware.nextcloud.client import NextcloudClient
from sync.groupware.nextcloud.connection import (
    CalendarMissingError,
    UnknownAddressError,
)
from sync.groupware.nextcloud.dav import XML_CONTENT_TYPE, DavClient
from sync.model import CalendarEvent, DatetimeInterval, FreeBusyStatus
from sync.nextcloud.ical import make_busy_block_name, read_busy_block, render_busy_block
from sync.nextcloud.spans import BusyPeriod

# A Monday far enough ahead that nothing else is booked then.
MONDAY_NINE = datetime(2030, 1, 7, 9, tzinfo=UTC)
MONDAY_TEN = datetime(2030, 1, 7, 10, tzinfo=UTC)
MONDAY_ELEVEN = datetime(2030, 1, 7, 11, tzinfo=UTC)
MONDAY_MIDNIGHT = datetime(2030, 1, 7, tzinfo=UTC)
TUESDAY_MIDNIGHT = datetime(2030, 1, 8, tzinfo=UTC)

PLANNING = CalendarEvent(
    original_id="planning",
    interval=DatetimeInterval(start=MONDAY_NINE, end=MONDAY_TEN),
    free_busy_status=FreeBusyStatus.BUSY,
)
PLANNING_BUSY_BLOCK_NAME = make_busy_block_name("planning")
PLANNING_BUSY_BLOCK = render_busy_block(PLANNING, MONDAY_MIDNIGHT)


async def test_a_prepared_ghost_calendar_is_shared_with_the_service(
    nextcloud_settings: NextcloudSettings,
    nextcloud: NextcloudClient,
    ghost: str,
    ghost_password: SecretStr,
) -> None:
    # Twice: preparing again after a failure must work.
    _ = await nextcloud.calendars.prepare_ghost_calendar(ghost, ghost_password)
    calendar_href = await nextcloud.calendars.prepare_ghost_calendar(
        ghost, ghost_password
    )

    calendar_hrefs = await nextcloud.calendars.list_ghost_calendar_hrefs()
    assert calendar_hrefs[ghost] == calendar_href
    assert calendar_href.startswith("/remote.php/dav/calendars/")
    assert nextcloud_settings.service_user not in calendar_hrefs


async def test_a_write_to_a_ghost_without_a_calendar_is_a_missing_calendar(
    nextcloud_settings: NextcloudSettings, nextcloud: NextcloudClient, ghost: str
) -> None:
    calendar_href = get_ghost_calendar_href(nextcloud_settings.service_user, ghost)

    with pytest.raises(CalendarMissingError):
        await nextcloud.calendars.put_object(
            calendar_href, PLANNING_BUSY_BLOCK_NAME, PLANNING_BUSY_BLOCK
        )


async def test_a_ghost_calendar_leaves_the_ghost_no_login_token(
    nextcloud_settings: NextcloudSettings,
    nextcloud: NextcloudClient,
    ghost: str,
    ghost_password: SecretStr,
) -> None:
    """A kept session would leave a login token per ghost, ~10k in all."""
    _ = await nextcloud.calendars.prepare_ghost_calendar(ghost, ghost_password)
    await nextcloud.calendars.release_ghost_calendar(ghost, ghost_password)

    # Nextcloud lists a user's tokens only on its security page and only to the
    # user's own session. The first visit logs in and the second one looks.
    ghost_auth = httpx.BasicAuth(ghost, ghost_password.get_secret_value())
    async with httpx.AsyncClient(
        base_url=nextcloud_settings.url, auth=ghost_auth
    ) as browser:
        _ = await browser.get("/index.php/settings/user/security")
        security_page = await browser.get("/index.php/settings/user/security")

    tokens_state = re.search(
        r'id="initial-state-settings-app_tokens" value="([^"]*)"',
        security_page.text,
    )
    assert tokens_state is not None
    tokens = TypeAdapter(list[dict[str, object]]).validate_json(
        base64.b64decode(tokens_state[1])
    )
    # Only the token of the session that looks.
    assert [token.get("current") for token in tokens] == [True]


async def test_busy_blocks_are_written_listed_and_deleted(
    nextcloud: NextcloudClient, ghost: str, ghost_password: SecretStr
) -> None:
    calendar_href = await nextcloud.calendars.prepare_ghost_calendar(
        ghost, ghost_password
    )

    await nextcloud.calendars.put_object(
        calendar_href, PLANNING_BUSY_BLOCK_NAME, PLANNING_BUSY_BLOCK
    )

    objects = await nextcloud.calendars.list_objects(
        calendar_href, MONDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert list(objects) == [PLANNING_BUSY_BLOCK_NAME]
    assert read_busy_block(objects[PLANNING_BUSY_BLOCK_NAME]) == BusyPeriod(
        start=MONDAY_NINE, end=MONDAY_TEN
    )
    later_objects = await nextcloud.calendars.list_objects(
        calendar_href, TUESDAY_MIDNIGHT, datetime(2030, 1, 9, tzinfo=UTC)
    )
    assert later_objects == {}

    await nextcloud.calendars.delete_object(calendar_href, PLANNING_BUSY_BLOCK_NAME)
    # Deleting what is gone already is no error.
    await nextcloud.calendars.delete_object(calendar_href, PLANNING_BUSY_BLOCK_NAME)

    assert not await nextcloud.calendars.list_objects(
        calendar_href, MONDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )


async def test_only_existing_users_exist(
    nextcloud: NextcloudClient, ghost: str
) -> None:
    assert await nextcloud.principals.user_exists(ghost)
    assert not await nextcloud.principals.user_exists(str(uuid4()))


async def test_busy_time_is_read_with_its_status(
    nextcloud: NextcloudClient, ghost: str, ghost_password: SecretStr
) -> None:
    address = f"{ghost}@example.com"
    await nextcloud.accounts.edit_user(ghost, "email", address)
    calendar_href = await nextcloud.calendars.prepare_ghost_calendar(
        ghost, ghost_password
    )
    lunch = CalendarEvent(
        original_id="lunch",
        interval=DatetimeInterval(start=MONDAY_TEN, end=MONDAY_ELEVEN),
        free_busy_status=FreeBusyStatus.TENTATIVE,
    )
    await nextcloud.calendars.put_object(
        calendar_href, PLANNING_BUSY_BLOCK_NAME, PLANNING_BUSY_BLOCK
    )
    await nextcloud.calendars.put_object(
        calendar_href,
        make_busy_block_name("lunch"),
        render_busy_block(lunch, MONDAY_MIDNIGHT),
    )

    busy_periods = await nextcloud.principals.get_busy_periods_by_type(
        address, MONDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    busy_intervals = await nextcloud.principals.get_busy_intervals(
        address, MONDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )

    assert busy_periods == [
        BusyPeriod(start=MONDAY_NINE, end=MONDAY_TEN, status=FreeBusyStatus.BUSY),
        BusyPeriod(
            start=MONDAY_TEN, end=MONDAY_ELEVEN, status=FreeBusyStatus.TENTATIVE
        ),
    ]
    assert busy_intervals is not None
    assert [(interval.start, interval.end) for interval in busy_intervals] == [
        (MONDAY_NINE, MONDAY_TEN),
        (MONDAY_TEN, MONDAY_ELEVEN),
    ]


async def test_an_unknown_address_is_an_error(nextcloud: NextcloudClient) -> None:
    with pytest.raises(UnknownAddressError):
        _ = await nextcloud.principals.get_busy_periods_by_type(
            f"{uuid4()}@example.com", MONDAY_MIDNIGHT, TUESDAY_MIDNIGHT
        )


async def test_a_released_ghost_calendar_is_gone(
    nextcloud: NextcloudClient, ghost: str, ghost_password: SecretStr
) -> None:
    calendar_href = await nextcloud.calendars.prepare_ghost_calendar(
        ghost, ghost_password
    )

    # Twice: releasing again after a failure must work.
    await nextcloud.calendars.release_ghost_calendar(ghost, ghost_password)
    await nextcloud.calendars.release_ghost_calendar(ghost, ghost_password)

    assert ghost not in await nextcloud.calendars.list_ghost_calendar_hrefs()
    with pytest.raises(CalendarMissingError):
        await nextcloud.calendars.put_object(
            calendar_href, PLANNING_BUSY_BLOCK_NAME, PLANNING_BUSY_BLOCK
        )
    with pytest.raises(CalendarMissingError):
        await nextcloud.calendars.delete_object(calendar_href, PLANNING_BUSY_BLOCK_NAME)
    with pytest.raises(CalendarMissingError):
        _ = await nextcloud.calendars.list_objects(
            calendar_href, MONDAY_MIDNIGHT, TUESDAY_MIDNIGHT
        )


async def test_a_delete_in_a_read_only_share_is_an_error(
    nextcloud_settings: NextcloudSettings,
    nextcloud: NextcloudClient,
    ghost: str,
    ghost_password: SecretStr,
) -> None:
    calendar_href = await nextcloud.calendars.prepare_ghost_calendar(
        ghost, ghost_password
    )
    await nextcloud.calendars.put_object(
        calendar_href, PLANNING_BUSY_BLOCK_NAME, PLANNING_BUSY_BLOCK
    )
    # The ghost shares again without `owncloud:read-write`, so the share is
    # read-only.
    read_only_share_body = (
        '<owncloud:share xmlns:dav="DAV:" xmlns:owncloud="http://owncloud.org/ns">'
        "<owncloud:set><dav:href>"
        f"principal:principals/users/{nextcloud_settings.service_user}"
        "</dav:href></owncloud:set></owncloud:share>"
    )
    ghost_auth = httpx.BasicAuth(ghost, ghost_password.get_secret_value())
    async with DavClient(nextcloud_settings.url, ghost_auth) as ghost_dav_client:
        response = await ghost_dav_client.request(
            "POST",
            f"/remote.php/dav/calendars/{ghost}/msync/",
            read_only_share_body,
            headers={"Content-Type": XML_CONTENT_TYPE},
        )
    assert response.status_code == 200

    # Nextcloud answers 404 although the busy block is still there.
    with pytest.raises(CalendarMissingError, match="read-only"):
        await nextcloud.calendars.delete_object(calendar_href, PLANNING_BUSY_BLOCK_NAME)
    assert list(
        await nextcloud.calendars.list_objects(
            calendar_href, MONDAY_MIDNIGHT, TUESDAY_MIDNIGHT
        )
    ) == [PLANNING_BUSY_BLOCK_NAME]
