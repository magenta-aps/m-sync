# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import secrets
from contextlib import suppress
from uuid import uuid4

import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs

from sync.config import NextcloudSettings
from sync.groupware.nextcloud.client import NextcloudClient
from sync.groupware.nextcloud.connection import (
    NextcloudError,
    NotFoundError,
    UserExistsError,
    quote_path_segment,
)
from sync.groupware.nextcloud.ocs import (
    NextcloudAccount,
    OcsClient,
    RealNextcloudAccounts,
)

GHOST_GROUP = "msync-ghosts"


async def test_a_new_ghost_has_no_address_and_is_in_the_ghost_group(
    nextcloud_client: NextcloudClient, ghost: str
) -> None:
    account = await nextcloud_client.accounts.get_account(ghost)

    assert account.id == ghost
    assert account.email is None
    assert await nextcloud_client.accounts.is_in_group(ghost, GHOST_GROUP)
    assert not await nextcloud_client.accounts.is_in_group(ghost, "msync-released")
    assert ghost in await nextcloud_client.accounts.list_members(GHOST_GROUP)


async def test_an_edited_address_reads_back(
    nextcloud_client: NextcloudClient, ghost: str
) -> None:
    await nextcloud_client.accounts.edit_user(ghost, "email", f"{ghost}@example.com")

    account = await nextcloud_client.accounts.get_account(ghost)

    assert account.email == f"{ghost}@example.com"


async def test_listing_ghosts_pages_through_all_of_them(
    nextcloud_settings: NextcloudSettings,
    manager_ocs_client: OcsClient,
    three_ghosts: list[str],
) -> None:
    # Two per page, so finding all three ghosts takes more than one page.
    # Other members of the group are not ours to assert about.
    accounts = RealNextcloudAccounts(
        manager_ocs_client, nextcloud_settings.url, page_size=2
    )

    ghosts = await accounts.list_ghosts(GHOST_GROUP)

    assert set(three_ghosts) <= ghosts.keys()


async def test_an_empty_group_has_no_ghosts(
    nextcloud_client: NextcloudClient, empty_group: str
) -> None:
    # Nextcloud sends the empty map of users as `[]`, PHP's empty array.
    assert await nextcloud_client.accounts.list_ghosts(empty_group) == {}


async def test_an_unreadable_ghost_is_skipped_and_the_rest_listed(
    nextcloud_client: NextcloudClient, ghost: str, admin_in_ghost_group: str
) -> None:
    with capture_logs() as logs:
        ghosts = await nextcloud_client.accounts.list_ghosts(GHOST_GROUP)

    assert ghost in ghosts
    assert admin_in_ghost_group not in ghosts
    assert {
        "event": "unreadable ghost",
        "user_id": admin_in_ghost_group,
        "log_level": "warning",
    } in logs


async def test_a_ghost_logs_in_with_a_new_password(
    nextcloud_client: NextcloudClient, ghost: str
) -> None:
    new_password = SecretStr(secrets.token_urlsafe())
    await nextcloud_client.accounts.set_password(ghost, new_password)

    _ = await nextcloud_client.calendars.prepare_ghost_calendar(ghost, new_password)

    assert ghost in await nextcloud_client.calendars.list_ghost_calendar_hrefs()


async def test_a_ghost_can_be_disabled_and_enabled_again(
    nextcloud_client: NextcloudClient, ghost: str
) -> None:
    await nextcloud_client.accounts.set_enabled(ghost, enabled=False)
    disabled_account = await nextcloud_client.accounts.get_account(ghost)
    await nextcloud_client.accounts.set_enabled(ghost, enabled=True)
    enabled_account = await nextcloud_client.accounts.get_account(ghost)

    assert not disabled_account.enabled
    assert enabled_account.enabled


async def test_a_disabled_ghost_cannot_log_in(
    nextcloud_client: NextcloudClient, ghost: str, ghost_password: SecretStr
) -> None:
    await nextcloud_client.accounts.set_enabled(ghost, enabled=False)

    with pytest.raises(NextcloudError):
        _ = await nextcloud_client.calendars.prepare_ghost_calendar(
            ghost, ghost_password
        )


async def test_a_user_sets_its_own_address_without_the_manager(
    nextcloud_client: NextcloudClient,
    admin_ocs_client: OcsClient,
    ghost_password: SecretStr,
) -> None:
    # Outside the ghost group, so the manager cannot edit it.
    user_id = str(uuid4())
    user_path = f"/ocs/v2.php/cloud/users/{quote_path_segment(user_id)}"
    form = {"userid": user_id, "password": ghost_password.get_secret_value()}
    try:
        _ = await admin_ocs_client.call_ocs(
            "POST", "/ocs/v2.php/cloud/users", object, form=form
        )
        await nextcloud_client.accounts.set_own_email(
            user_id, ghost_password, f"{user_id}@example.com"
        )
        answer = await admin_ocs_client.call_ocs("GET", user_path, NextcloudAccount)
    finally:
        # A failed create can still leave the user behind.
        with suppress(NotFoundError):
            _ = await admin_ocs_client.call_ocs("DELETE", user_path, object)

    assert answer.data.email == f"{user_id}@example.com"


async def test_the_manager_does_not_see_users_outside_its_groups(
    nextcloud_settings: NextcloudSettings, nextcloud_client: NextcloudClient
) -> None:
    service_user = nextcloud_settings.service_user

    assert not await nextcloud_client.accounts.is_in_group(service_user, GHOST_GROUP)
    with pytest.raises(NotFoundError):
        _ = await nextcloud_client.accounts.get_account(service_user)


async def test_creating_a_user_that_exists_is_a_user_exists_error(
    nextcloud_client: NextcloudClient, ghost: str, ghost_password: SecretStr
) -> None:
    with pytest.raises(UserExistsError):
        await nextcloud_client.accounts.create_user(
            ghost, "Test Ghost", ghost_password, GHOST_GROUP
        )


async def test_an_unknown_user_is_not_found(nextcloud_client: NextcloudClient) -> None:
    with pytest.raises(NotFoundError):
        _ = await nextcloud_client.accounts.get_account(str(uuid4()))


@pytest.mark.usefixtures("manager_is_sub_admin")
async def test_an_answer_that_is_not_ocs_is_an_error_that_does_not_quote_it(
    manager_ocs_client: OcsClient,
) -> None:
    with pytest.raises(NextcloudError) as raised:
        # Nextcloud's status page answers JSON that is not OCS.
        _ = await manager_ocs_client.call_ocs("GET", "/status.php", object)

    assert str(raised.value) == "GET /status.php: unexpected answer, HTTP 200"
    assert raised.value.__suppress_context__
