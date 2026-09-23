# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import asyncio

import structlog

from sync.config import DirectoryServiceName, Settings, get_settings
from sync.connectors.microsoft_cloud import MicrosoftCloudConnector
from sync.protocols import DirectoryServiceConnector

from .connectors.dummy import DummyConnector
from .core import Core
from .model import GroupwareSystemName

logger = structlog.stdlib.get_logger()


async def main() -> None:
    settings = get_settings()
    directory_service = _make_dir_service_connector(settings)
    core = Core(directory_service)
    system_alpha = DummyConnector(GroupwareSystemName("alpha"), core)
    system_beta = DummyConnector(GroupwareSystemName("beta"), core)
    core.add_connectors(system_alpha, system_beta)

    async with asyncio.TaskGroup() as tg:
        _ = tg.create_task(system_alpha.run())
        _ = tg.create_task(system_beta.run())
        _ = tg.create_task(_refresh_users_loop(core))


def _make_dir_service_connector(settings: Settings) -> DirectoryServiceConnector:
    match settings.directory_service:
        case DirectoryServiceName.DUMMY:
            raise NotImplementedError
        case DirectoryServiceName.MICROSOFT_CLOUD:
            return MicrosoftCloudConnector(settings)


async def _refresh_users_loop(core: Core) -> None:
    while True:
        logger.info("refreshing user list")
        await core.refresh_user_list()
        await asyncio.sleep(5)
