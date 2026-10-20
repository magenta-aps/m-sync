# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.functions import now

from sync.groupware.microsoft_cloud.connector import MicrosoftCloudGroupware
from sync.groupware.microsoft_cloud.database import users_table
from tests.helpers import DummyMsGraph


async def test_database_schema_creation(
    empty_db: async_sessionmaker[AsyncSession],
) -> None:
    """On startup, the connector should ensure the schema has been created."""

    connector = MicrosoftCloudGroupware(DummyMsGraph(), empty_db)
    await connector.run()

    async with empty_db() as session:
        # then we check that the schema is what we expect by inserting a row
        _ = await session.execute(
            insert(users_table).values(
                id="user-1",
                status="active",
                updated_at=now(),
                full_sync_due_at=now(),
                lock_expires_at=now(),
                retry_count=0,
            )
        )
