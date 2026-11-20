# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from textwrap import dedent
from typing import Literal, final

import structlog
from sqlalchemy import (
    Column,
    ColumnElement,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    TypedColumns,
    case,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import ENUM, UUID, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.schema import CreateSchema
from sqlalchemy.sql.functions import now

from sync.model import User
from sync.ms_graph import DeltaLink

logger = structlog.stdlib.get_logger()

_metadata = MetaData(schema="groupware_microsoft_cloud")

_user_status_enum = ENUM("active", "inactive", name="user_status", metadata=_metadata)

UserStatus = Literal["active", "inactive"]


@final
class UsersColumns(TypedColumns):
    id = Column(Text, primary_key=True)
    email = Column(Text, nullable=True)
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
    sync_due_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=now(),
        comment=dedent(
            """\
            When the next sync for this user is due. This includes any backoff
            if the last attempt failed.
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
            the backoff applied to sync_due_at.
            """
        ),
    )
    last_full_sync_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Last time a full sync succeded.",
    )
    last_delta_sync_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Last time a delta sync succeded.",
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


async def update_user_status(session: AsyncSession, active_users: set[User]) -> None:
    """Given a list of active users, marks users as active/inactive, or inserts
    them if they don't exist."""

    user_ids = {user.id for user in active_users}

    # mark inactive users
    _ = await session.execute(
        update(users_table)
        .where(
            users_table.c.id.not_in(user_ids),
            users_table.c.status != "inactive",
        )
        .values(status="inactive")
    )

    # exit early to avoid INSERT with no values, which is not allowed
    if not active_users:
        logger.info("deactivated all users, because no users were passed")
        return

    # upsert all users that are in `users`, marking them as active
    _ = await session.execute(
        insert(users_table)
        .values(
            [
                {"id": user.id, "email": user.mail, "status": "active"}
                for user in active_users
            ]
        )
        .on_conflict_do_update(
            index_elements=[users_table.c.id],
            # inactive users get all their scheduling metadata reset, whereas
            # users that are already active are unaffected
            where=users_table.c.status == "inactive",
            set_={
                "status": "active",
                "updated_at": now(),
                "sync_due_at": now(),
                "lock_expires_at": now(),
                "locked_by": None,
                "retry_count": 0,
            },
        )
    )


@dataclass(frozen=True)
class UserLock:
    """A handle to an active user synchronization job."""

    user: User
    lock_id: uuid.UUID
    delta_link: DeltaLink | None
    last_full_sync: datetime
    last_delta_sync: datetime


async def acquire_user_job(session: AsyncSession) -> UserLock | None:
    """Acquire a lock on a user that needs to be synced, returning its ID.

    Returns `None` if there are no pending jobs, or if one could not be selected
    due to contention."""

    user_is_active = users_table.c.status == "active"
    user_needs_syncing = users_table.c.sync_due_at <= now()
    user_is_not_locked = users_table.c.locked_by.is_(None) | (
        users_table.c.lock_expires_at <= now()
    )

    next_job_subquery = (
        select(users_table.c.id)
        .where(user_is_active & user_needs_syncing & user_is_not_locked)
        .order_by(users_table.c.updated_at)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )

    lock_id = uuid.uuid4()

    backoff = _make_backoff(
        users_table.c.retry_count, start_delay_secs=1, max_delay_hours=3
    )
    # TODO: if we ever need it, we can apply jitter by multiplying with func.random()

    res = await session.execute(
        update(users_table)
        .where(users_table.c.id == next_job_subquery)
        .values(
            updated_at=now(),
            sync_due_at=now() + backoff,
            locked_by=lock_id,
            lock_expires_at=now() + _make_interval(secs=60),
            retry_count=users_table.c.retry_count + 1,
        )
        .returning(
            users_table.c.id,
            users_table.c.email,
            users_table.c.delta_link,
            users_table.c.locked_by,
            users_table.c.last_delta_sync_at,
            users_table.c.last_full_sync_at,
        )
    )

    maybe_row = res.first()
    if maybe_row is None:
        logger.debug("no job to acquire")
        return None
    id, email, delta_link, locked_by, last_delta_sync_at, last_full_sync_at = maybe_row
    assert locked_by == lock_id, "sanity check"

    return UserLock(
        user=User(id=id, mail=email),
        lock_id=lock_id,
        delta_link=DeltaLink(delta_link) if delta_link else None,
        last_delta_sync=last_delta_sync_at,
        last_full_sync=last_full_sync_at,
    )


async def commit_user_job(
    session: AsyncSession,
    lock: UserLock,
    new_delta_link: DeltaLink,
    polling_period: timedelta,
    *,
    full_sync: bool,
) -> None:
    """Unlock the user row, marking it as succesful.

    If the lock has expired, this does nothing, as another job already took the
    lock.

    The `full_sync` flag selects whether the `last_full_sync_at` or `last_delta_sync_at`
    should be updated.
    """

    _ = await session.execute(
        update(users_table)
        .values(
            updated_at=now(),
            sync_due_at=now() + _make_interval(secs=polling_period.total_seconds()),
            locked_by=None,
            retry_count=0,
            delta_link=str(new_delta_link),
            **{
                "last_full_sync_at" if full_sync else "last_delta_sync_at": now(),
            },
        )
        .where(
            users_table.c.id == lock.user.id, users_table.c.locked_by == lock.lock_id
        )
    )


async def abort_user_job(
    session: AsyncSession,
    lock: UserLock,
    *,
    invalidate_delta_link: bool = False,
) -> None:
    """Release the lock, without committing anything."""

    _ = await session.execute(
        update(users_table)
        .values(
            updated_at=now(),
            locked_by=None,
            **({"delta_link": None} if invalidate_delta_link else {}),
        )
        .where(
            users_table.c.id == lock.user.id, users_table.c.locked_by == lock.lock_id
        )
    )


def _make_backoff(
    retry_count: ColumnElement[int], start_delay_secs: float, max_delay_hours: int
) -> ColumnElement[timedelta]:
    """Makes an SQL expression for computing exponential backoff.

    Given the retry count `[0, 1, 2, ...]`, it produces the following sequence:

    ```
    [0, start, start * 2, start * 4, ..., max, max, max, ...]
    ```

    """

    scale = case(
        # on the zeroeth retry, we don't apply any backoff
        (retry_count == 0, 0),
        # the -1 is so we start the backoff at 2^0 = 1
        # the 31 is a cap so we avoid overflow
        else_=func.pow(2, func.least(retry_count - 1, 31)),
    )

    return func.least(
        _make_interval(secs=start_delay_secs) * scale,
        _make_interval(hours=max_delay_hours),
    )


def _make_interval(hours: int = 0, secs: float = 0) -> ColumnElement[timedelta]:
    # this is a workaround for this issue: https://github.com/sqlalchemy/sqlalchemy/discussions/8474
    return func.make_interval(0, 0, 0, 0, hours, 0, secs)
