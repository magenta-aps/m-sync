# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


from textwrap import dedent
from typing import Literal, final

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    TypedColumns,
)
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.schema import CreateSchema
from sqlalchemy.sql.functions import now

_metadata = MetaData(schema="groupware_microsoft_cloud")

_user_status_enum = ENUM("active", "inactive", name="user_status", metadata=_metadata)

UserStatus = Literal["active", "inactive"]


@final
class UsersColumns(TypedColumns):
    id = Column(Text, primary_key=True)
    status = Column[UserStatus](
        _user_status_enum,
        nullable=False,
        comment=dedent(
            """\
            Whether the user should be synced. Rows for inactive users are
            kept, but skipped by workers.
            """
        ),
    )
    delta_link = Column(
        Text,
        comment=dedent(
            """\
            Delta token passed to Microsoft to only fetch changes since the
            last sync.
            """
        ),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=now(),
        onupdate=now(),
        comment=dedent(
            """\
            Last time this row was modified. Used to prioritize when many
            rows are due to be synced at the same time.
            """
        ),
    )
    full_sync_due_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=now(),
        comment=dedent(
            """\
            When the next full sync for this user is due. This includes any
            backoff if the last attempt failed.
            """
        ),
    )
    locked_by = Column(
        UUID(as_uuid=True),
        comment=dedent(
            """\
            Lease token of the worker holding the lock. A worker only writes
            results if it still owns the lock.
            """
        ),
    )
    lock_expires_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=now(),
        comment=dedent(
            """\
            Time at which the lock may be reacquired, allowing recovery if a
            worker crashed while holding it.
            """
        ),
    )
    retry_count = Column(
        Integer,
        default=0,
        nullable=False,
        comment=dedent(
            """\
            Number of failed sync attempts since the last success. Drives
            the backoff applied to full_sync_due_at.
            """
        ),
    )


users_table = Table(
    "users",
    _metadata,
    UsersColumns,
    comment=dedent(
        """\
        Queue of Microsoft Cloud users due for syncing. The order of jobs in the
        queue is implicitly determined by how workers sort them on read.
        """
    ),
)


async def create_schema_if_not_exists(session: AsyncSession) -> None:
    """Create the schema, enum and tables if they do not already exist."""
    _ = await session.execute(
        CreateSchema("groupware_microsoft_cloud", if_not_exists=True)
    )
    await session.run_sync(
        lambda session: _metadata.create_all(bind=session.connection(), checkfirst=True)
    )
    await session.commit()
