# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


import asyncio
from asyncio import Event, TaskGroup
from datetime import UTC, datetime, timedelta
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.routing import Mount

from ...model import FreeBusy, GroupwareSystemName, User
from ...ms_graph import MsGraph
from .database import (
    UserLock,
    abort_user_job,
    acquire_user_job,
    create_schema_if_not_exists,
    update_user_status,
)

logger = structlog.stdlib.get_logger()

_MAX_CONCURRENCY: Final = 5

# how long to wait when there are no jobs to run, before checking again
_QUEUE_EMPTY_POLL_PERIOD: Final = timedelta(seconds=2)

# how long to wait between succesful full syncs, 25 is chosen to avoid lining
# up with other 24-hour cycles on the same machine
_FULL_SYNC_PERIOD: Final = timedelta(hours=25)

# how long to wait between succesful delta syncs
_DELTA_SYNC_PERIOD: Final = timedelta(seconds=5)


class MicrosoftCloudGroupware:
    _client: MsGraph
    _database: async_sessionmaker[AsyncSession]

    def __init__(
        self, client: MsGraph, database: async_sessionmaker[AsyncSession]
    ) -> None:
        self._client = client
        self._database = database

    async def run(self, stop: Event | None = None) -> None:
        async with self._database() as session:
            await create_schema_if_not_exists(session)
        async with TaskGroup() as tg:
            for id in range(_MAX_CONCURRENCY):
                _ = tg.create_task(self._worker(id, stop))
        logger.info("all workers stopped successfully")

    def get_groupware_system_name(self) -> GroupwareSystemName:
        return GroupwareSystemName("microsoft_cloud")

    def get_webhooks_mount(self) -> Mount:
        return Mount("/ms_cloud", routes=[])

    async def update_user_list(self, users: set[User]) -> None:
        async with self._database() as session:
            await update_user_status(session, {user.id for user in users})
            await session.commit()

    async def send_free_busy(self, user: User, update: FreeBusy) -> None:
        _ = user
        _ = update
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
            if _full_sync_due(lock):
                await self._full_sync(worker_id, lock)
            else:
                await self._delta_sync(worker_id, lock)

        logger.info("stop signal received", worker_id=worker_id)

    async def _full_sync(self, worker_id: int, lock: UserLock):
        logger.debug(
            "performing full sync",
            worker_id=worker_id,
            user_id=lock.row_id,
            lock_id=lock.lock_id,
        )
        # TODO: actual implementation here
        async with self._database() as session:
            await abort_user_job(session, lock)
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


def _full_sync_due(lock: UserLock) -> bool:
    # TODO: if thundering herd ever becomes a problem, we can pick a random number
    #   for each user, which is updated only when a full sync is performed, and
    #   we use that to pick the full sync period randomly
    return (
        lock.delta_link is None
        or lock.last_full_sync + _FULL_SYNC_PERIOD <= datetime.now(UTC)
    )
