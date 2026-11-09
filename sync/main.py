# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import asyncio
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager

import structlog
import uvicorn
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from sync.config import DirectoryServiceName, Settings, get_settings
from sync.directories.microsoft_cloud import MicrosoftCloudDirectory

from .core import Core
from .database import check_database, make_database_engine
from .model import AppState, Request
from .ms_graph import RealMsGraph
from .protocols import DirectoryServiceConnector, GroupwareConnector

logger = structlog.stdlib.get_logger()


def main() -> None:
    """Entry point, constructs and starts the Starlette application based on
    configuration."""

    settings = get_settings()
    database_engine = make_database_engine()
    directory_service = _make_dir_service_connector(settings)
    core = Core(directory_service)

    app = make_app(settings, database_engine, core, [])
    uvicorn.run(app, host="0.0.0.0", port=5000)


def make_app(
    _settings: Settings,
    database_engine: AsyncEngine,
    core: Core,
    groupware: Sequence[GroupwareConnector],
) -> Starlette:
    """Construct the Starlette web application. Components are injected as arguments
    to allow tests to pass in mocked implementations."""

    routes: list[BaseRoute] = []
    for connector in groupware:
        if mount := connector.get_webhooks_mount():
            routes.append(mount)
    routes.append(Route("/health", _health))
    routes.append(Route("/health/database", _database_health))

    return Starlette(
        routes=routes,
        lifespan=lambda app: _make_app_lifetime(app, core, groupware, database_engine),
    )


@asynccontextmanager
async def _make_app_lifetime(
    _app: Starlette,
    core: Core,
    groupware: Sequence[GroupwareConnector],
    database_engine: AsyncEngine,
) -> AsyncGenerator[AppState]:
    try:
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(connector.run()) for connector in groupware]
            tasks.append(tg.create_task(_refresh_users_loop(core)))
            yield {"db": database_engine}
            for task in tasks:
                _ = task.cancel()
    finally:
        _ = await database_engine.dispose()


def _make_dir_service_connector(settings: Settings) -> DirectoryServiceConnector:
    match settings.directory_service:
        case DirectoryServiceName.DUMMY:
            raise NotImplementedError
        case DirectoryServiceName.MICROSOFT_CLOUD:
            return MicrosoftCloudDirectory(RealMsGraph(settings))


async def _refresh_users_loop(core: Core) -> None:
    while True:
        logger.info("refreshing user list")
        await core.update_user_list()
        await asyncio.sleep(5)


async def _health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def _database_health(request: Request) -> JSONResponse:
    db = request.state["db"]
    try:
        await check_database(db)
    except Exception:
        logger.warning("database health check failed", exc_info=True)
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok"})
