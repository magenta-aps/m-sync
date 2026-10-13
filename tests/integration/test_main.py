# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from starlette.applications import Starlette
from starlette.testclient import TestClient

from sync.config import Settings
from sync.core import Core
from sync.main import make_app
from tests.helpers import DummyDirectoryService


def test_health_endpoint_is_reachable(settings: Settings) -> None:
    directory_service = DummyDirectoryService()
    core = Core(directory_service)

    app: Starlette = make_app(settings, core, [])

    # Intentionally not used as a context manager, so the lifespan (and
    # thus the directory/groupware run loops) is not started.
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
