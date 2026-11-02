# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.functions import now

from sync.config import Settings
from sync.groupware.microsoft_cloud.connector import MicrosoftCloudGroupware
from sync.groupware.microsoft_cloud.database import (
    create_schema_if_not_exists,
    users_table,
)
from sync.model import User
from sync.protocols import GroupwareConnector
from tests.helpers import DummyMsGraph


@pytest.fixture
async def database(
    empty_db: async_sessionmaker[AsyncSession],
) -> async_sessionmaker[AsyncSession]:
    async with empty_db() as session:
        await create_schema_if_not_exists(session)
    return empty_db


async def test_database_schema_creation(
    empty_db: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Calling `setup` should ensure the schema has been created."""

    connector = MicrosoftCloudGroupware(DummyMsGraph(), empty_db, settings)

    await connector.setup()

    async with empty_db() as session:
        # then we check that the schema is what we expect by inserting a row
        _ = await session.execute(
            insert(users_table).values(
                id="user-1",
                status="active",
                updated_at=now(),
                sync_due_at=now(),
                lock_expires_at=now(),
                retry_count=0,
            )
        )


async def test_update_user_list(
    database: async_sessionmaker[AsyncSession], settings: Settings
):
    """Tests that users can be updated through the GroupwareConnector interface."""

    groupware: GroupwareConnector = MicrosoftCloudGroupware(
        DummyMsGraph(), database, settings
    )

    await groupware.update_user_list({User(name="gneurshk", id="user-1")})

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
