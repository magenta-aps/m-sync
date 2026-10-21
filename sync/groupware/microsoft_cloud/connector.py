# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.routing import Mount

from ...model import FreeBusy, GroupwareSystemName, User
from ...ms_graph import MsGraph
from .database import create_schema_if_not_exists

logger = structlog.stdlib.get_logger()


class MicrosoftCloudGroupware:
    _client: MsGraph
    _database: async_sessionmaker[AsyncSession]

    def __init__(
        self, client: MsGraph, database: async_sessionmaker[AsyncSession]
    ) -> None:
        self._client = client
        self._database = database

    async def run(self) -> None:
        async with self._database() as session:
            await create_schema_if_not_exists(session)

        # TODO: start up workers

    def get_groupware_system_name(self) -> GroupwareSystemName:
        raise NotImplementedError

    def get_webhooks_mount(self) -> Mount:
        raise NotImplementedError

    async def update_user_list(self, users: list[User]) -> None:
        _ = users
        raise NotImplementedError

    async def send_free_busy(self, user: User, update: FreeBusy) -> None:
        _ = user
        _ = update
        raise NotImplementedError
