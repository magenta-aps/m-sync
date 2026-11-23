# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sync.config import Settings


def make_database_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url, connect_args={"connect_timeout": 5}
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def check_database(engine: AsyncEngine) -> None:
    """Verify database connectivity, raising an exception if it is down."""
    async with engine.connect() as conn:
        _ = await conn.execute(text("SELECT 1"))
