# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import asyncio

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from sync.config import Settings
from sync.core import Core
from sync.database import make_database_engine
from sync.groupware.nextcloud.client import RealNextcloudClient
from sync.groupware.nextcloud.connector import NextcloudGroupware
from sync.main import make_app
from tests.helpers import DummyDirectoryService


def test_health_endpoint_is_reachable(settings: Settings) -> None:
    database_engine = make_database_engine(settings)
    directory_service = DummyDirectoryService()
    core = Core(directory_service)

    app: Starlette = make_app(settings, database_engine, core, [])

    # Intentionally not used as a context manager, so the lifespan (and
    # thus the directory/groupware run loops) is not started.
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_app_closes_the_nextcloud_client_when_it_stops(settings: Settings) -> None:
    assert settings.nextcloud is not None, "missing Nextcloud configuration"
    database_engine = make_database_engine(settings)
    nextcloud_client = RealNextcloudClient(settings.nextcloud)
    connector = NextcloudGroupware(nextcloud_client, settings.nextcloud)
    core = Core(DummyDirectoryService(), connector)

    app: Starlette = make_app(settings, database_engine, core, [connector])

    # Used as a context manager, so the lifespan runs the connector and stops it.
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    # A closed client refuses to send before it reaches Nextcloud.
    with pytest.raises(RuntimeError, match="client has been closed"):
        _ = asyncio.run(nextcloud_client.principals.user_exists("any-user"))
