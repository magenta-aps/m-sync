# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import asyncio
from collections.abc import Sequence
from contextlib import asynccontextmanager

import structlog
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from sync.config import DirectoryServiceName, Settings, get_settings
from sync.directories.microsoft_cloud import MicrosoftCloudDirectory

from .core import Core
from .groupware.dummy import DummyGroupware
from .microsoft_cloud import make_graph_client
from .model import GroupwareSystemName
from .protocols import DirectoryServiceConnector, GroupwareConnector

logger = structlog.stdlib.get_logger()


def main() -> None:
    """Entry point, constructs and starts the Starlette application based on
    configuration."""

    settings = get_settings()
    directory_service = _make_dir_service_connector(settings)
    core = Core(directory_service)
    groupware = [
        DummyGroupware(GroupwareSystemName("alpha"), core),
        DummyGroupware(GroupwareSystemName("beta"), core),
    ]
    core.add_groupware(*groupware)

    app = make_app(settings, core, groupware)
    uvicorn.run(app, host="0.0.0.0", port=5000)


def make_app(
    _settings: Settings,
    core: Core,
    groupware: Sequence[GroupwareConnector],
) -> Starlette:
    """Construct the Starlette web application."""

    routes = []
    for connector in groupware:
        if mount := connector.get_webhooks_mount():
            routes.append(mount)
    routes.append(Route("/health", _health))

    return Starlette(
        routes=routes, lifespan=lambda app: _make_app_lifetime(app, core, groupware)
    )


@asynccontextmanager
async def _make_app_lifetime(
    _app: Starlette, core: Core, groupware: Sequence[GroupwareConnector]
):
    async with asyncio.TaskGroup() as tg:
        for connector in groupware:
            _ = tg.create_task(connector.run())
        _ = tg.create_task(_refresh_users_loop(core))
        yield


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


async def _health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})
