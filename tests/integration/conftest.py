# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
from dotenv import dotenv_values
from more_itertools import one
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sync.config import NextcloudSettings, Settings, get_settings
from sync.database import make_sessionmaker
from sync.groupware.nextcloud.client import NextcloudClient, RealNextcloudClient
from sync.model import User
from sync.ms_graph import DeltaLink, MsGraph, RealMsGraph


@pytest.fixture(autouse=True)
def _load_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in dotenv_values(".env").items():
        if value is None:
            continue
        monkeypatch.setenv(key, value)


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def _admin_db(settings: Settings) -> Iterator[Connection]:
    """We need to be connected to the "postgres" database in order to create and
    delete other databases."""

    url = make_url(settings.database_url).set(database="postgres")
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.begin() as connection:
        yield connection
    engine.dispose()


@pytest.fixture
async def empty_db(
    settings: Settings,
    _admin_db: Connection,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Creates an empty dedicated database for running tests in."""

    _ = _admin_db.execute(text('DROP DATABASE IF EXISTS "test" WITH (FORCE)'))
    _ = _admin_db.execute(text('CREATE DATABASE "test"'))

    url = make_url(settings.database_url).set(database="test")
    engine = create_async_engine(url)
    yield make_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
def ms_graph(settings: Settings) -> MsGraph:
    return RealMsGraph(settings)


@pytest.fixture
def nextcloud_settings(settings: Settings) -> NextcloudSettings:
    assert settings.nextcloud is not None, "missing Nextcloud configuration"
    return settings.nextcloud


@pytest.fixture
async def nextcloud_client(
    nextcloud_settings: NextcloudSettings,
) -> AsyncIterator[NextcloudClient]:
    async with RealNextcloudClient(nextcloud_settings) as nextcloud_client:
        yield nextcloud_client


@pytest.fixture
async def ms_user(ms_graph: MsGraph) -> User:
    """Returns a default test user in our test Microsoft Cloud instance, with
    all of its calendar data wiped ahead of time, so tests do not need to
    trust that other tests clean up after themselves."""

    group = one(
        [
            group
            async for group in ms_graph.list_groups("msync-")
            if group.name == "msync-microsoft_cloud-nextcloud"
        ]
    )
    assert group.id is not None
    user = one(
        [
            user
            async for user in ms_graph.list_group_members(group.id)
            if user.mail == "laurits-testensen@magentakommune.dk"
        ]
    )

    # delete everything in this window
    window_start = datetime(2000, 1, 1, tzinfo=UTC)
    window_end = datetime(2100, 1, 1, tzinfo=UTC)
    async for event in ms_graph.list_calendar_events(user.id, window_start, window_end):
        if isinstance(event, DeltaLink):
            break
        event_id, _calendar_event = event
        await ms_graph.delete_event(user.id, event_id)

    return user
