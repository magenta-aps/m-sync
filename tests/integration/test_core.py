# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sync.config import Settings
from sync.core import Core
from sync.directories.microsoft_cloud import MicrosoftCloudDirectory
from sync.groupware.microsoft_cloud.connector import MicrosoftCloudGroupware
from sync.groupware.microsoft_cloud.database import (
    create_schema_if_not_exists,
    users_table,
)
from sync.model import GroupwareSystemName, User
from tests.helpers import DummyGroupware, DummyMsGraph


@pytest.fixture
async def database(
    empty_db: async_sessionmaker[AsyncSession],
) -> async_sessionmaker[AsyncSession]:
    # each connector's schema can be added here
    async with empty_db() as session:
        await create_schema_if_not_exists(session)
    return empty_db


async def test_refresh_user_list_from_directory(
    database: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Test that user list from directory reaches groupware, passing through core."""

    client = DummyMsGraph()
    client.group_ids["msync-microsoft_cloud-dummy"] = "group-1"
    client.group_members["group-1"] = [User(name="genurshk", id="user-1")]

    directory = MicrosoftCloudDirectory(client)
    groupware = MicrosoftCloudGroupware(client, database, settings)

    core = Core(directory)
    core.add_groupware(groupware)
    core.add_groupware(DummyGroupware(GroupwareSystemName("dummy")))

    await core.update_user_list()

    async with database() as session:
        res = await session.execute(
            select(
                users_table.c.id,
                users_table.c.status,
            )
        )
        row = res.first()
        assert row is not None
        id_, status = row
        assert id_ == "user-1"
        assert status == "active"
