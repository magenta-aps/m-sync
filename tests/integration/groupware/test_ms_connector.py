# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


import asyncio
from asyncio import Event
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.functions import now
from tenacity import retry, stop_after_delay, wait_fixed

from sync.config import Settings
from sync.core import Core
from sync.groupware.microsoft_cloud.connector import MicrosoftCloudGroupware
from sync.groupware.microsoft_cloud.database import (
    create_schema_if_not_exists,
    users_table,
)
from sync.model import (
    MICROSOFT_CLOUD,
    CalendarEvent,
    DatetimeInterval,
    EventSource,
    FreeBusyStatus,
    GroupwareSystemName,
    User,
)
from sync.ms_graph import MsGraph
from sync.protocols import GroupwareConnector
from tests.helpers import DummyDirectoryService, DummyGroupware, DummyMsGraph


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

    connector = MicrosoftCloudGroupware(
        DummyMsGraph(), empty_db, settings, Core(DummyDirectoryService())
    )

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
        DummyMsGraph(), database, settings, Core(DummyDirectoryService())
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


async def test_full_sync_scheduled(
    ms_graph: MsGraph,
    ms_user: User,
    database: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Tests that a full-sync can be scheduled and reaches Core.

    Sets up an event on the real Microsoft calendar, then sets up a real Microsoft
    connector and a dummy "mirror" connector. When the Microsoft connector starts
    up (on an empty database), it should schedule a full sync almost immediately.

    We then check that Core has delivered the "create_event" to the dummy connector.
    """

    # an event native to Microsoft, within the window covered by full-syncs
    # (the past week through the next year)
    start = datetime.now(UTC) + timedelta(days=30)
    end = start + timedelta(hours=1)
    event = CalendarEvent(
        source=None,
        interval=DatetimeInterval(start=start, end=end),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    event_id = await ms_graph.create_event(ms_user.id, event)
    directory = DummyDirectoryService()
    directory.group_ids["msync-microsoft_cloud-dummy"] = "group-1"
    directory.group_members["group-1"] = [ms_user]
    mirror = DummyGroupware(GroupwareSystemName("dummy"))
    core = Core(directory, mirror)
    groupware = MicrosoftCloudGroupware(ms_graph, database, settings, core)

    # queueing the user makes the full-sync due almost immediately
    await groupware.update_user_list({ms_user})
    stop = Event()
    run_task = asyncio.create_task(groupware.run(stop))

    @retry(
        wait=wait_fixed(0.5),
        stop=stop_after_delay(10),
        reraise=True,
    )
    async def assert_full_sync_reached_mirror() -> None:
        """The full-sync is asynchronous, so the assertions may fail just
        because it has not completed yet. Retrying them for a while gives it
        time to finish."""

        created = mirror.created_events.get(ms_user)
        assert created is not None, "the full-sync never reached the mirror"

        # the event is native to Microsoft, so the ID Microsoft assigned it
        # doubles as the original ID, and it round-trips through Microsoft
        # unchanged
        assert created == [
            CalendarEvent(
                source=EventSource(system=MICROSOFT_CLOUD, original_id=event_id),
                interval=event.interval,
                free_busy_status=FreeBusyStatus.BUSY,
            )
        ]

    try:
        await assert_full_sync_reached_mirror()
    finally:
        stop.set()
        await run_task
