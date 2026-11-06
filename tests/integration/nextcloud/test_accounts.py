# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import secrets
from uuid import uuid4

import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs

from sync.config import NextcloudSettings
from sync.groupware.nextcloud.client import NextcloudClient
from sync.groupware.nextcloud.connection import NextcloudError, NotFoundError
from sync.groupware.nextcloud.ocs import OcsClient, RealNextcloudAccounts

GHOST_GROUP = "msync-ghosts"


async def test_a_new_ghost_has_no_address_and_is_in_the_ghost_group(
    nextcloud: NextcloudClient, ghost: str
) -> None:
    account = await nextcloud.accounts.get_account(ghost)

    assert account.id == ghost
    assert account.email is None
    assert await nextcloud.accounts.is_in_group(ghost, GHOST_GROUP)
    assert not await nextcloud.accounts.is_in_group(ghost, "msync-released")
    assert ghost in await nextcloud.accounts.list_members(GHOST_GROUP)


async def test_an_edited_address_reads_back(
    nextcloud: NextcloudClient, ghost: str
) -> None:
    await nextcloud.accounts.edit_user(ghost, "email", f"{ghost}@example.com")

    account = await nextcloud.accounts.get_account(ghost)

    assert account.email == f"{ghost}@example.com"


async def test_listing_ghosts_pages_through_all_of_them(
    manager_ocs_client: OcsClient, three_ghosts: list[str]
) -> None:
    # Two per page, so finding all three ghosts takes more than one page.
    # Other members of the group are not ours to assert about.
    accounts = RealNextcloudAccounts(manager_ocs_client, page_size=2)

    ghosts = await accounts.list_ghosts(GHOST_GROUP)

    assert set(three_ghosts) <= ghosts.keys()


async def test_an_empty_group_has_no_ghosts(
    nextcloud: NextcloudClient, empty_group: str
) -> None:
    # Nextcloud sends the empty map of users as `[]`, PHP's empty array.
    assert await nextcloud.accounts.list_ghosts(empty_group) == {}


async def test_an_unreadable_ghost_is_skipped_and_the_rest_listed(
    nextcloud: NextcloudClient, ghost: str, admin_in_ghost_group: str
) -> None:
    with capture_logs() as logs:
        ghosts = await nextcloud.accounts.list_ghosts(GHOST_GROUP)

    assert ghost in ghosts
    assert admin_in_ghost_group not in ghosts
    assert {
        "event": "unreadable ghost",
        "user_id": admin_in_ghost_group,
        "log_level": "warning",
    } in logs


async def test_a_ghost_logs_in_with_a_new_password(
    nextcloud: NextcloudClient, ghost: str
) -> None:
    new_password = SecretStr(secrets.token_urlsafe())
    await nextcloud.accounts.set_password(ghost, new_password)

    await nextcloud.calendars.prepare_ghost_calendar(ghost, new_password)

    assert ghost in await nextcloud.calendars.list_ghost_calendar_hrefs()


async def test_the_manager_does_not_see_users_outside_its_groups(
    nextcloud_settings: NextcloudSettings, nextcloud: NextcloudClient
) -> None:
    service_user = nextcloud_settings.service_user

    assert not await nextcloud.accounts.is_in_group(service_user, GHOST_GROUP)
    with pytest.raises(NotFoundError):
        _ = await nextcloud.accounts.get_account(service_user)


async def test_an_unknown_user_is_not_found(nextcloud: NextcloudClient) -> None:
    with pytest.raises(NotFoundError):
        _ = await nextcloud.accounts.get_account(str(uuid4()))


async def test_an_answer_that_is_not_ocs_is_an_error_that_does_not_quote_it(
    manager_ocs_client: OcsClient,
) -> None:
    with pytest.raises(NextcloudError) as raised:
        # Nextcloud's status page answers JSON that is not OCS.
        _ = await manager_ocs_client.call_ocs("GET", "/status.php", object)

    assert str(raised.value) == "GET /status.php: unexpected answer, HTTP 200"
    assert raised.value.__suppress_context__
