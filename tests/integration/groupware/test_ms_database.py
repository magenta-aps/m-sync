# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


from datetime import UTC, datetime

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.functions import now

from sync.groupware.microsoft_cloud.database import (
    create_schema_if_not_exists,
    update_user_status,
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
                sync_due_at=now(),
                lock_expires_at=now(),
                retry_count=0,
            )
        )


async def test_update_user_status_new(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Updating a non-existent user should create a new entry for that user, with
    the default values for its columns."""

    async with database() as session:
        await update_user_status(session, active_users={"abc123"})

        res = await session.execute(
            select(
                users_table.c.id,
                users_table.c.status,
                users_table.c.delta_link,
                users_table.c.locked_by,
                users_table.c.retry_count,
            )
        )
        row = res.first()
        assert row is not None
        id_, status, delta_link, locked_by, retry_count = row
        assert id_ == "abc123"
        assert status == "active"
        assert delta_link is None
        assert locked_by is None
        assert retry_count == 0


async def test_update_user_status_idempotent(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Activating a user that is already active does nothing."""

    async with database() as session:
        _ = await session.execute(
            insert(users_table).values(
                [
                    {
                        "id": "abc123",
                        "status": "active",
                        "updated_at": datetime(year=2020, month=1, day=1, tzinfo=UTC),
                        "retry_count": 5,
                    }
                ]
            )
        )

        await update_user_status(session, active_users={"abc123"})

        res = await session.execute(
            select(
                users_table.c.id,
                users_table.c.status,
                users_table.c.updated_at,
                users_table.c.retry_count,
            )
        )
        row = res.first()
        assert row is not None
        id_, status, updated_at, retry_count = row
        assert id_ == "abc123"
        assert status == "active"
        assert updated_at == datetime(year=2020, month=1, day=1, tzinfo=UTC)
        assert retry_count == 5


async def test_update_user_status_deactivate_all(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Passing an empty set deactivates all users."""

    async with database() as session:
        _ = await session.execute(
            insert(users_table).values(
                [
                    {
                        "id": "abc123",
                        "status": "active",
                    }
                ]
            )
        )

        await update_user_status(session, active_users=set())

        res = await session.execute(
            select(
                users_table.c.id,
                users_table.c.status,
            )
        )
        row = res.first()
        assert row is not None
        id_, status = row
        assert id_ == "abc123"
        assert status == "inactive"


async def test_update_user_status_deactivate_some(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Deactivating only some users, should leave the rest unaffected."""

    async with database() as session:
        _ = await session.execute(
            insert(users_table).values(
                [
                    {
                        "id": "user-1",
                        "status": "active",
                    },
                    {
                        "id": "user-2",
                        "status": "active",
                    },
                ]
            )
        )

        await update_user_status(session, active_users={"user-1"})

        res = await session.execute(
            select(
                users_table.c.id,
                users_table.c.status,
            )
        )
        row1, row2 = res.all()

        assert row1 is not None
        id1, status1 = row1
        assert id1 == "user-1"
        assert status1 == "active"

        assert row2 is not None
        id2, status2 = row2
        assert id2 == "user-2"
        assert status2 == "inactive"


async def test_update_user_status_reactivate(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Re-activating a de-activated user resets only scheduling metadata."""

    async with database() as session:
        _ = await session.execute(
            insert(users_table).values(
                [
                    {
                        "id": "abc123",
                        "status": "inactive",
                        "delta_link": "example.com",
                        "retry_count": 5,
                    }
                ]
            )
        )

        await update_user_status(session, active_users={"abc123"})

        res = await session.execute(
            select(
                users_table.c.id,
                users_table.c.status,
                users_table.c.delta_link,
                users_table.c.retry_count,
            )
        )
        row = res.first()
        assert row is not None
        id_, status, delta_link, retry_count = row
        assert id_ == "abc123"
        assert status == "active"
        assert delta_link == "example.com"  # <- unchanged
        assert retry_count == 0  # <- this was reset
