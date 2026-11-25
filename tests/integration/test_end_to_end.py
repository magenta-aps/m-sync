# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

"""These tests exercise the entire app, without mocking anything."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.applications import Starlette
from tenacity import retry, stop_after_delay, wait_fixed

from sync.config import Settings
from sync.core import Core
from sync.database import make_sessionmaker
from sync.directories.microsoft_cloud import MicrosoftCloudDirectory
from sync.groupware.microsoft_cloud.connector import MicrosoftCloudGroupware
from sync.groupware.nextcloud.client import NextcloudClient
from sync.groupware.nextcloud.connector import NextcloudGroupware
from sync.main import make_app
from sync.model import (
    MICROSOFT_CLOUD,
    CalendarEvent,
    DatetimeInterval,
    FreeBusyStatus,
    User,
)
from sync.ms_graph import MsGraph
from sync.nextcloud.ical import read_busy_block
from tests.helpers import async_test_client


@pytest.fixture
def ms_and_nc_app(
    settings: Settings,
    ms_graph: MsGraph,
    nextcloud_client: NextcloudClient,
    empty_db_engine: AsyncEngine,
) -> Starlette:
    assert settings.nextcloud is not None, "missing Nextcloud settings"
    dir = MicrosoftCloudDirectory(ms_graph)
    core = Core(dir)
    ms = MicrosoftCloudGroupware(
        ms_graph, make_sessionmaker(empty_db_engine), settings, core
    )
    nc = NextcloudGroupware(nextcloud_client, settings.nextcloud)
    core.add_groupware(ms, nc)
    return make_app(settings, empty_db_engine, core, [ms, nc])


async def test_app_can_be_started(ms_and_nc_app: Starlette) -> None:
    """Builds a real production-like app, and checks its health endpoints."""

    async with async_test_client(ms_and_nc_app) as client:
        response = await client.get("/health")

    assert response.status_code == 200


async def test_full_sync_create_one_event(
    ms_and_nc_app: Starlette,
    ms_graph: MsGraph,
    ms_user: User,
    nextcloud_client: NextcloudClient,
) -> None:
    # use `now` so that it is within the sync window
    t0 = datetime.now(UTC)
    t1 = t0 + timedelta(days=3)
    event = CalendarEvent(
        source=None,
        interval=DatetimeInterval(start=t0, end=t1),
        free_busy_status=FreeBusyStatus.BUSY,
    )

    _event_id = await ms_graph.create_event(ms_user.id, event)

    @retry(wait=wait_fixed(0.5), stop=stop_after_delay(30), reraise=True)
    async def assert_full_sync_reached_nextcloud() -> None:
        # check that the ghost account was created
        ghost = await nextcloud_client.accounts.get_account(ms_user.id)
        assert ghost.email == ms_user.mail
        assert not ghost.enabled

        # check that the ghost href was created
        calendar_hrefs = await nextcloud_client.calendars.list_ghost_calendar_hrefs()
        assert ms_user.id in calendar_hrefs

        # check that the event was created as a busy block in the window
        objects = await nextcloud_client.calendars.list_objects(
            calendar_hrefs[ms_user.id], t0, t1
        )
        busy_events = [
            busy_event
            for calendar in objects.values()
            if (busy_event := read_busy_block(calendar)) is not None
        ]
        assert len(busy_events) == 1
        mirrored = busy_events[0]
        source = mirrored.get_source()
        assert source.system == MICROSOFT_CLOUD
        assert mirrored.free_busy_status == FreeBusyStatus.BUSY

        # Nextcloud writes whole seconds, so compare within a second.
        interval = mirrored.interval
        assert abs((interval.start_datetime() - t0).total_seconds()) < 1
        assert abs((interval.end_datetime() - t1).total_seconds()) < 1

    async with async_test_client(ms_and_nc_app):
        await assert_full_sync_reached_nextcloud()
