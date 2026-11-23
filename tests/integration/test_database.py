# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from starlette.applications import Starlette
from starlette.testclient import TestClient

from sync.config import Settings
from sync.core import Core
from sync.database import make_database_engine
from sync.main import make_app
from tests.helpers import DummyDirectoryService


def test_database_health_endpoint(settings: Settings) -> None:
    database_engine = make_database_engine(settings)
    directory_service = DummyDirectoryService()
    core = Core(directory_service)
    app: Starlette = make_app(settings, database_engine, core, [])
    with TestClient(app) as client:
        response = client.get("/health/database")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
