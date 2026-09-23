# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


import pytest
from dotenv import dotenv_values

from sync.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _load_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in dotenv_values(".env").items():
        if value is None:
            continue
        monkeypatch.setenv(key, value)


@pytest.fixture
def settings() -> Settings:
    return get_settings()
