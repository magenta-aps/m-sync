# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import os
import secrets
from collections.abc import AsyncIterator
from contextlib import suppress
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from dotenv import dotenv_values
from pydantic import SecretStr

from sync.config import NextcloudSettings
from sync.groupware.nextcloud.client import NextcloudClient
from sync.groupware.nextcloud.connection import NotFoundError, quote_path_segment
from sync.groupware.nextcloud.ocs import (
    NextcloudAccounts,
    OcsClient,
    RealNextcloudAccounts,
)

GHOST_GROUP = "msync-ghosts"
MANAGED_GROUPS = [GHOST_GROUP, "msync-released"]


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _manager_is_sub_admin() -> AsyncIterator[None]:
    """The manager is a sub-admin of the ghost groups, as it is in production. occ
    has no command for it, so the Nextcloud admin sets it over OCS and removes it
    after. `.env` wins over the environment, as in `_load_env`."""
    dotenv = {key: value for key, value in dotenv_values(".env").items() if value}
    env = dict(os.environ) | dotenv
    admin_user = env.get("NEXTCLOUD_ADMIN_USER")
    admin_password = env.get("NEXTCLOUD_ADMIN_PASSWORD")
    nextcloud_url = env.get("MSYNC__NEXTCLOUD__URL")
    manager_user = env.get("MSYNC__NEXTCLOUD__MANAGER_USER")
    if not nextcloud_url or not manager_user:
        pytest.fail("missing Nextcloud configuration")
    if not admin_user or not admin_password:
        # Without an admin the manager must already be a sub-admin.
        yield
        return
    path = f"/ocs/v2.php/cloud/users/{quote_path_segment(manager_user)}/subadmins"
    admin_auth = httpx.BasicAuth(admin_user, admin_password)
    async with OcsClient(nextcloud_url, admin_auth) as admin:
        answer = await admin.call_ocs("GET", path, list[str])
        # Only what we add, so a Nextcloud set up by hand keeps its sub-admins.
        added = [group for group in MANAGED_GROUPS if group not in answer.data]
        for group in added:
            _ = await admin.call_ocs("POST", path, object, form={"groupid": group})
        yield
        for group in added:
            _ = await admin.call_ocs("DELETE", path, object, form={"groupid": group})


@pytest.fixture
def manager_auth(nextcloud_settings: NextcloudSettings) -> httpx.BasicAuth:
    return httpx.BasicAuth(
        nextcloud_settings.manager_user,
        nextcloud_settings.manager_password.get_secret_value(),
    )


@pytest.fixture
async def manager_ocs_client(
    nextcloud_settings: NextcloudSettings, manager_auth: httpx.BasicAuth
) -> AsyncIterator[OcsClient]:
    async with OcsClient(nextcloud_settings.url, manager_auth) as ocs_client:
        yield ocs_client


@pytest.fixture
def admin_auth() -> httpx.BasicAuth | None:
    """The Nextcloud admin from `.env`. None without one."""
    admin_user = os.environ.get("NEXTCLOUD_ADMIN_USER")
    admin_password = os.environ.get("NEXTCLOUD_ADMIN_PASSWORD")
    if not admin_user or not admin_password:
        return None
    return httpx.BasicAuth(admin_user, admin_password)


@pytest.fixture
async def admin_ocs_client(
    nextcloud_settings: NextcloudSettings, admin_auth: httpx.BasicAuth | None
) -> AsyncIterator[OcsClient]:
    """The Nextcloud admin. Tests that need it are skipped without one in `.env`."""
    if admin_auth is None:
        pytest.skip("no Nextcloud admin in .env")
    async with OcsClient(nextcloud_settings.url, admin_auth) as ocs_client:
        yield ocs_client


@pytest.fixture
async def cleanup_accounts(
    nextcloud_settings: NextcloudSettings,
    admin_auth: httpx.BasicAuth | None,
    manager_auth: httpx.BasicAuth,
) -> AsyncIterator[NextcloudAccounts]:
    """Deletes test users as the admin if `.env` has one, else as the manager.
    A create that fails half-way leaves the user outside the ghost group. Only
    the admin can delete it there."""
    async with OcsClient(
        nextcloud_settings.url, admin_auth or manager_auth
    ) as ocs_client:
        yield RealNextcloudAccounts(ocs_client, nextcloud_settings.url)


@pytest.fixture
async def empty_group(
    nextcloud_settings: NextcloudSettings, admin_ocs_client: OcsClient
) -> AsyncIterator[str]:
    """A new group without members, which the manager is a sub-admin of."""
    group = f"msync-test-{uuid4()}"
    manager_user = quote_path_segment(nextcloud_settings.manager_user)
    form = {"groupid": group}
    try:
        _ = await admin_ocs_client.call_ocs(
            "POST", "/ocs/v2.php/cloud/groups", object, form=form
        )
        _ = await admin_ocs_client.call_ocs(
            "POST",
            f"/ocs/v2.php/cloud/users/{manager_user}/subadmins",
            object,
            form=form,
        )
        yield group
    finally:
        # Deleting the group also ends the manager's sub-admin rights in it.
        with suppress(NotFoundError):
            _ = await admin_ocs_client.call_ocs(
                "DELETE",
                f"/ocs/v2.php/cloud/groups/{quote_path_segment(group)}",
                object,
            )


@pytest.fixture
async def admin_in_ghost_group(admin_ocs_client: OcsClient) -> AsyncIterator[str]:
    """A new admin in the ghost group. The manager sees only its id."""
    user_id = str(uuid4())
    form = {
        "userid": user_id,
        "password": secrets.token_urlsafe(),
        "groups[]": "admin",
    }
    try:
        _ = await admin_ocs_client.call_ocs(
            "POST", "/ocs/v2.php/cloud/users", object, form=form
        )
        _ = await admin_ocs_client.call_ocs(
            "POST",
            f"/ocs/v2.php/cloud/users/{quote_path_segment(user_id)}/groups",
            object,
            form={"groupid": GHOST_GROUP},
        )
        yield user_id
    finally:
        # A failed create can still leave the user behind.
        with suppress(NotFoundError):
            _ = await admin_ocs_client.call_ocs(
                "DELETE",
                f"/ocs/v2.php/cloud/users/{quote_path_segment(user_id)}",
                object,
            )


@pytest.fixture
def ghost_password() -> SecretStr:
    return SecretStr(secrets.token_urlsafe())


@pytest.fixture
async def ghost(
    nextcloud: NextcloudClient,
    cleanup_accounts: NextcloudAccounts,
    ghost_password: SecretStr,
) -> AsyncIterator[str]:
    """A new ghost without an address, deleted again with its calendars."""
    user_id = str(uuid4())
    try:
        await nextcloud.accounts.create_user(
            user_id, "Test Ghost", ghost_password, GHOST_GROUP
        )
        yield user_id
    finally:
        # Also after a failed create, which may have left the user behind.
        with suppress(NotFoundError):
            await cleanup_accounts.delete_user(user_id)


@pytest.fixture
async def three_ghosts(
    nextcloud: NextcloudClient,
    cleanup_accounts: NextcloudAccounts,
    ghost_password: SecretStr,
) -> AsyncIterator[list[str]]:
    ghost_ids = [str(uuid4()) for _ in range(3)]
    try:
        for ghost_id in ghost_ids:
            await nextcloud.accounts.create_user(
                ghost_id, "Test Ghost", ghost_password, GHOST_GROUP
            )
        yield ghost_ids
    finally:
        for ghost_id in ghost_ids:
            # Also after a failed create, which may have left the user behind.
            with suppress(NotFoundError):
                await cleanup_accounts.delete_user(ghost_id)
