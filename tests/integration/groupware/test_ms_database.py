# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.functions import now

from sync.groupware.microsoft_cloud.database import (
    abort_user_job,
    acquire_user_job,
    commit_user_job,
    create_schema_if_not_exists,
    update_user_status,
    users_table,
)
from sync.ms_graph import DeltaLink

_LONG_AGO = datetime(year=2020, month=1, day=1, tzinfo=UTC)
_FUTURE = datetime.now(tz=UTC) + timedelta(days=1)
_TOLERANCE = timedelta(seconds=5)


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


async def test_acquire_user_job_empty(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """With no users in the table, no job can be acquired."""

    async with database() as session:
        assert await acquire_user_job(session) is None


async def test_acquire_user_job(database: async_sessionmaker[AsyncSession]) -> None:
    """An active user that is due for syncing can be locked."""

    async with database() as session:
        _ = await session.execute(
            insert(users_table).values(
                id="user-1",
                status="active",
                sync_due_at=_LONG_AGO,
            )
        )

        lock = await acquire_user_job(session)

        assert lock is not None
        assert lock.row_id == "user-1"

        res = await session.execute(
            select(
                users_table.c.id,
                users_table.c.status,
                users_table.c.locked_by,
                users_table.c.lock_expires_at,
                users_table.c.retry_count,
            )
        )
        row = res.first()
        assert row is not None
        id, status, locked_by, lock_expires_at, retry_count = row
        assert id == "user-1"
        assert status == "active"
        assert locked_by == lock.lock_id
        assert lock_expires_at > datetime.now(UTC)
        assert retry_count == 1


async def test_acquire_user_job_expired(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Tests that a locked row can be acquired if the lock is expired."""

    async with database() as session:
        _ = await session.execute(
            insert(users_table).values(
                id="user-1",
                status="active",
                locked_by=uuid.uuid4(),
                sync_due_at=_LONG_AGO,
                lock_expires_at=_LONG_AGO,
            )
        )

        lock = await acquire_user_job(session)

        assert lock is not None
        assert lock.row_id == "user-1"

        res = await session.execute(
            select(
                users_table.c.id,
                users_table.c.status,
                users_table.c.locked_by,
                users_table.c.lock_expires_at,
                users_table.c.retry_count,
            )
        )
        row = res.first()
        assert row is not None
        id, status, locked_by, lock_expires_at, retry_count = row
        assert id == "user-1"
        assert status == "active"
        assert locked_by == lock.lock_id
        assert lock_expires_at > datetime.now(UTC)
        assert retry_count == 1


async def test_acquire_user_job_fails_if_not_due(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Tests that a lock can't be acquired if the job is not due."""

    async with database() as session:
        _ = await session.execute(
            insert(users_table).values(
                id="user-1",
                status="active",
                sync_due_at=_FUTURE,
            )
        )

        lock = await acquire_user_job(session)

        assert lock is None


async def test_acquire_user_job_fails_if_locked(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Tests that a lock can't be acquired if it's already locked."""

    async with database() as session:
        _ = await session.execute(
            insert(users_table).values(
                id="user-1",
                status="active",
                sync_due_at=_LONG_AGO,
                locked_by=uuid.uuid4(),
                lock_expires_at=_FUTURE,
            )
        )

        lock = await acquire_user_job(session)

        assert lock is None


async def test_acquire_user_job_fails_if_inactive(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Tests that a lock can't be acquired if the user is inactive."""

    async with database() as session:
        _ = await session.execute(
            insert(users_table).values(
                id="user-1",
                status="inactive",
                sync_due_at=_LONG_AGO,
            )
        )

        lock = await acquire_user_job(session)

        assert lock is None


async def test_acquire_user_job_ordering(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """When multiple users are due, the least recently updated one is picked."""

    async with database() as session:
        _ = await session.execute(
            insert(users_table).values(
                [
                    {
                        "id": "user-1",
                        "status": "active",
                        "sync_due_at": _LONG_AGO,
                        "updated_at": _LONG_AGO + timedelta(days=1),
                    },
                    {
                        "id": "user-2",
                        "status": "active",
                        "sync_due_at": _LONG_AGO,
                        "updated_at": _LONG_AGO,
                    },
                ]
            )
        )

        lock = await acquire_user_job(session)

        assert lock is not None
        assert lock.row_id == "user-2"


async def test_acquire_user_job_backoff(
    database: async_sessionmaker[AsyncSession],
) -> None:
    """Acquiring a job that has been retried before pushes the next sync into
    the future."""

    async with database() as session:
        _ = await session.execute(
            insert(users_table).values(
                id="user-1",
                status="active",
                sync_due_at=_LONG_AGO,
                retry_count=4,
            )
        )

        lock = await acquire_user_job(session)

        assert lock is not None
        assert lock.row_id == "user-1"

        res = await session.execute(
            select(
                users_table.c.id,
                users_table.c.sync_due_at,
                users_table.c.retry_count,
            )
        )
        row = res.first()
        assert row is not None
        id, sync_due_at, retry_count = row
        assert id == "user-1"
        assert retry_count == 5

        # backoff for retry_count=4 is 2^3 = 8 seconds
        expected = datetime.now(UTC) + timedelta(seconds=8)
        assert abs(sync_due_at - expected) < _TOLERANCE


@pytest.mark.parametrize("full_sync", [True, False])
async def test_commit_user_job(
    database: async_sessionmaker[AsyncSession], full_sync: bool
) -> None:
    """Committing a job unlocks the row and schedules the next sync."""

    async with database() as session:
        _ = await session.execute(
            insert(users_table).values(
                id="user-1",
                status="active",
                sync_due_at=_LONG_AGO,
                retry_count=4,
            )
        )

        lock = await acquire_user_job(session)
        assert lock is not None

        await commit_user_job(
            session,
            lock,
            new_delta_link=DeltaLink("https://example.com/delta"),
            polling_period=timedelta(hours=1),
            full_sync=full_sync,
        )

        res = await session.execute(
            select(
                users_table.c.id,
                users_table.c.status,
                users_table.c.delta_link,
                users_table.c.sync_due_at,
                users_table.c.locked_by,
                users_table.c.retry_count,
                users_table.c.last_delta_sync_at,
                users_table.c.last_full_sync_at,
            )
        )
        row = res.first()
        assert row is not None
        (
            id,
            status,
            delta_link,
            sync_due_at,
            locked_by,
            retry_count,
            last_delta_sync_at,
            last_full_sync_at,
        ) = row

        assert id == "user-1"
        assert status == "active"
        assert delta_link == "https://example.com/delta"
        assert locked_by is None
        assert retry_count == 0
        sync_due_at_expected = datetime.now(UTC) + timedelta(hours=1)
        assert abs(sync_due_at - sync_due_at_expected) < _TOLERANCE
        last_sync_expected = datetime.now(UTC)
        if full_sync:
            assert abs(last_full_sync_at - last_sync_expected) < _TOLERANCE
        else:
            assert abs(last_delta_sync_at - last_sync_expected) < _TOLERANCE


@pytest.mark.parametrize("full_sync", [True, False])
async def test_commit_user_job_lost_lock(
    database: async_sessionmaker[AsyncSession], full_sync: bool
) -> None:
    """If the lock has been taken over by another worker, committing does
    nothing."""

    async with database() as session:
        _ = await session.execute(
            insert(users_table).values(
                id="user-1",
                status="active",
                sync_due_at=_LONG_AGO,
                retry_count=4,
            )
        )

        lock = await acquire_user_job(session)
        assert lock is not None

        # simulate another worker taking over the lock
        other_lock = uuid.uuid4()
        _ = await session.execute(
            users_table.update()
            .where(users_table.c.id == lock.row_id)
            .values(locked_by=other_lock)
        )
        _ = await session.commit()

        await commit_user_job(
            session,
            lock,
            new_delta_link=DeltaLink("https://example.com"),
            polling_period=timedelta(hours=1),
            full_sync=full_sync,
        )

        res = await session.execute(
            select(
                users_table.c.id,
                users_table.c.status,
                users_table.c.delta_link,
                users_table.c.locked_by,
                users_table.c.retry_count,
                users_table.c.last_delta_sync_at,
                users_table.c.last_full_sync_at,
            )
        )
        row = res.first()
        assert row is not None
        (
            id,
            status,
            delta_link,
            locked_by,
            retry_count,
            last_delta_sync_at,
            last_full_sync_at,
        ) = row

        assert id == "user-1"
        assert status == "active"
        assert delta_link is None
        assert locked_by == other_lock
        # the commit did not apply, so the retry count from the acquire remains
        assert retry_count == 5
        assert last_delta_sync_at is None
        assert last_full_sync_at is None


async def test_abort_user_job(database: async_sessionmaker[AsyncSession]) -> None:
    """Aborting a job releases the lock without touching anything else."""

    async with database() as session:
        _ = await session.execute(
            insert(users_table).values(
                id="user-1",
                status="active",
                sync_due_at=_LONG_AGO,
                delta_link="https://example.com/old",
            )
        )

        lock = await acquire_user_job(session)
        assert lock is not None

        await abort_user_job(session, lock)

        res = await session.execute(
            select(
                users_table.c.id,
                users_table.c.delta_link,
                users_table.c.locked_by,
            )
        )
        row = res.first()
        assert row is not None
        id, delta_link, locked_by = row

        assert id == "user-1"
        assert locked_by is None
        assert delta_link == "https://example.com/old"
