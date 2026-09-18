# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import asyncio

import structlog

from .connectors.dummy import DummyConnector
from .core import Core
from .model import GroupwareSystem

logger = structlog.stdlib.get_logger()


async def main() -> None:
    # TODO: this is a hard-coded example
    core = Core()
    system_alpha = DummyConnector(GroupwareSystem("alpha"), core)
    system_beta = DummyConnector(GroupwareSystem("beta"), core)
    core.add_connectors([system_alpha, system_beta])

    async with asyncio.TaskGroup() as tg:
        _ = tg.create_task(system_alpha.run())
        _ = tg.create_task(system_beta.run())
        _ = tg.create_task(_refresh_users_loop(core))


async def _refresh_users_loop(core: Core) -> None:
    while True:
        logger.info("refreshing user list")
        await core.refresh_user_list()
        await asyncio.sleep(5)
