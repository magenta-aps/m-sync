# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


from collections.abc import AsyncIterator, Iterator

import pytest
from dotenv import dotenv_values
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sync.config import Settings, get_settings
from sync.database import DATABASE_URL, make_sessionmaker


@pytest.fixture(autouse=True)
def _load_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in dotenv_values(".env").items():
        if value is None:
            continue
        monkeypatch.setenv(key, value)


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="session")
def _admin_db() -> Iterator[Connection]:
    """We need to be connected to the "postgres" database in order to create and
    delete other databases."""

    url = make_url(DATABASE_URL).set(database="postgres")
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.begin() as connection:
        yield connection
    engine.dispose()


@pytest.fixture
async def empty_db(
    _admin_db: Connection,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Creates an empty dedicated database for running tests in."""

    _ = _admin_db.execute(text('DROP DATABASE IF EXISTS "test" WITH (FORCE)'))
    _ = _admin_db.execute(text('CREATE DATABASE "test"'))

    url = make_url(DATABASE_URL).set(database="test")
    engine = create_async_engine(url)
    yield make_sessionmaker(engine)
    await engine.dispose()
