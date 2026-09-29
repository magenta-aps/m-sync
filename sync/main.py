# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import asyncio

import structlog

from sync.config import DirectoryServiceName, Settings, get_settings
from sync.directories.microsoft_cloud import MicrosoftCloudDirectory

from .core import Core
from .groupware.dummy import DummyGroupware
from .microsoft_cloud import make_graph_client
from .model import GroupwareSystemName
from .protocols import DirectoryServiceConnector

logger = structlog.stdlib.get_logger()


async def main() -> None:
    settings = get_settings()
    directory_service = _make_dir_service_connector(settings)
    core = Core(directory_service)
    system_alpha = DummyGroupware(GroupwareSystemName("alpha"), core)
    system_beta = DummyGroupware(GroupwareSystemName("beta"), core)
    core.add_groupware(system_alpha, system_beta)

    async with asyncio.TaskGroup() as tg:
        _ = tg.create_task(system_alpha.run())
        _ = tg.create_task(system_beta.run())
        _ = tg.create_task(_refresh_users_loop(core))


def _make_dir_service_connector(settings: Settings) -> DirectoryServiceConnector:
    match settings.directory_service:
        case DirectoryServiceName.DUMMY:
            raise NotImplementedError
        case DirectoryServiceName.MICROSOFT_CLOUD:
            return MicrosoftCloudDirectory(make_graph_client(settings))


async def _refresh_users_loop(core: Core) -> None:
    while True:
        logger.info("refreshing user list")
        await core.refresh_user_list()
        await asyncio.sleep(5)
