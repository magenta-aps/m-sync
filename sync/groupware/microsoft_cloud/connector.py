# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


import asyncio
from asyncio import Event, TaskGroup
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.routing import Mount

from sync.core import Core

from ...config import MicrosoftCloudSettings, Settings
from ...model import (
    CalendarEvent,
    EventPatch,
    EventSource,
    GroupwareSystemName,
    User,
)
from ...ms_graph import DeltaLink, MsGraph
from .database import (
    UserLock,
    abort_user_job,
    acquire_user_job,
    commit_user_job,
    create_schema_if_not_exists,
    update_user_status,
)

logger = structlog.stdlib.get_logger()

_MAX_CONCURRENCY: Final = 5

# how long to wait when there are no jobs to run, before checking again
_QUEUE_EMPTY_POLL_PERIOD: Final = timedelta(seconds=2)

# how long to wait between succesful delta syncs
_DELTA_SYNC_PERIOD: Final = timedelta(seconds=5)


class MicrosoftCloudGroupware:
    _client: MsGraph
    _database: async_sessionmaker[AsyncSession]
    _settings: MicrosoftCloudSettings
    _core: Core

    def __init__(
        self,
        client: MsGraph,
        database: async_sessionmaker[AsyncSession],
        settings: Settings,
        core: Core,
    ) -> None:
        self._client = client
        self._database = database
        assert settings.microsoft_cloud is not None
        self._settings = settings.microsoft_cloud
        self._core = core

    async def setup(self) -> None:
        async with self._database() as session:
            await create_schema_if_not_exists(session)

    async def run(self, stop: Event | None = None) -> None:
        async with TaskGroup() as tg:
            for id in range(_MAX_CONCURRENCY):
                _ = tg.create_task(self._worker(id, stop))
        logger.info("all workers stopped successfully")

    def get_groupware_system_name(self) -> GroupwareSystemName:
        return GroupwareSystemName("microsoft_cloud")

    def get_webhooks_mount(self) -> Mount:
        return Mount("/ms_cloud", routes=[])

    def get_events(
        self,
        user: User,
        window_start: datetime,
        window_end: datetime,
    ) -> AsyncIterator[CalendarEvent]:
        _ = user
        _ = window_start
        _ = window_end
        raise NotImplementedError

    async def update_user_list(self, users: set[User]) -> None:
        async with self._database() as session:
            await update_user_status(session, {user.id for user in users})
            await session.commit()

    async def create_events(self, user: User, events: list[CalendarEvent]) -> None:
        _ = user
        _ = events
        raise NotImplementedError

    async def delete_events(self, user: User, events: list[EventSource]) -> None:
        _ = user
        _ = events
        raise NotImplementedError

    async def update_events(self, user: User, patches: list[EventPatch]) -> None:
        _ = user
        _ = patches
        raise NotImplementedError

    async def _worker(self, worker_id: int, stop: Event | None) -> None:
        logger.info("started worker", worker_id=worker_id)
        while stop is None or not stop.is_set():
            async with self._database() as session:
                lock = await acquire_user_job(session)
                await session.commit()
            if lock is None:
                await asyncio.sleep(_QUEUE_EMPTY_POLL_PERIOD.total_seconds())
                continue
            if _full_sync_due(lock, self._settings):
                await self._full_sync(worker_id, lock)
            else:
                await self._delta_sync(worker_id, lock)

        logger.info("stop signal received", worker_id=worker_id)

    async def _full_sync(self, worker_id: int, lock: UserLock):
        # FIXME: Der mangler noget error-handling her, men tænker jeg vil fixe
        #   når vi kan teste ordenligt. F.eks. hvis Graph API returner en internal
        #   server error, så vil det her bare crashe.

        logger.debug(
            "performing full sync",
            worker_id=worker_id,
            user_id=lock.row_id,
            lock_id=lock.lock_id,
        )

        now = datetime.now(UTC)
        window_start = now - timedelta(days=7)
        window_end = now + timedelta(days=30 * 4)  # 4 months
        delta_link = None
        events: list[CalendarEvent] = []
        async for item in self._client.list_calendar_events(
            user_id=lock.row_id,
            window_start=window_start,
            window_end=window_end,
        ):
            if isinstance(item, DeltaLink):
                delta_link = item
                break
            _, event = item
            events.append(event)
        assert delta_link is not None

        user = User(id=lock.row_id)
        await self._core.full_sync(
            self.get_groupware_system_name(), user, events, window_start, window_end
        )

        async with self._database() as session:
            await commit_user_job(
                session,
                lock,
                new_delta_link=delta_link,
                polling_period=self._settings.full_sync_period,
                full_sync=True,
            )
            await session.commit()

    async def _delta_sync(self, worker_id: int, lock: UserLock):
        logger.debug(
            "performing delta sync",
            worker_id=worker_id,
            user_id=lock.row_id,
            lock_id=lock.lock_id,
        )
        # TODO: actual implementation here
        async with self._database() as session:
            await abort_user_job(session, lock)
            await session.commit()


def _full_sync_due(lock: UserLock, settings: MicrosoftCloudSettings) -> bool:
    # TODO: if thundering herd ever becomes a problem, we can pick a random number
    #   for each user, which is updated only when a full sync is performed, and
    #   we use that to pick the full sync period randomly
    return (
        lock.delta_link is None
        or lock.last_full_sync + settings.full_sync_period <= datetime.now(UTC)
    )
