# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.functions import now

from sync.groupware.microsoft_cloud.database import (
    create_schema_if_not_exists,
    users_table,
)


@pytest.fixture
async def database(
    empty_db: async_sessionmaker[AsyncSession],
) -> async_sessionmaker[AsyncSession]:
    async with empty_db() as session:
        await create_schema_if_not_exists(session)
    return empty_db


async def test_database_schema(database: async_sessionmaker[AsyncSession]) -> None:
    # we first sanity-check that the fixture actuallly gave us an empty database

    async with database() as session:
        res = await session.execute(select(users_table))
        assert res.first() is None

        # then we check that the schema is what we expect by inserting a row
        _ = await session.execute(
            insert(users_table).values(
                id="user-1",
                status="active",
                updated_at=now(),
                full_sync_due_at=now(),
                lock_expires_at=now(),
                retry_count=0,
            )
        )
