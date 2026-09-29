# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

"""Dummy connector for testing."""

# TODO: move to conftest when we have made some real connectors

import asyncio
from datetime import datetime, timedelta, timezone
from random import choice
from uuid import uuid4

import structlog

from ..core import Core
from ..model import (
    EventCreate,
    FreeBusy,
    GroupwareSystemName,
    Interval,
    UpdateSource,
    User,
)

logger = structlog.stdlib.get_logger()


class DummyGroupware:
    users: list[User]
    core: Core
    name: GroupwareSystemName

    def __init__(self, name: GroupwareSystemName, core: Core) -> None:
        self.users = []
        self.name = name
        self.core = core

    def get_groupware_system_name(self) -> GroupwareSystemName:
        return self.name

    async def run(self) -> None:
        while True:
            await asyncio.sleep(2)
            if len(self.users) == 0:
                continue

            # simulate an event on a random user
            now = datetime.now(timezone.utc)
            user = choice(self.users)
            source = UpdateSource(system=self.name, original_id=uuid4())
            update = EventCreate(
                source=source,
                timestamp=now,
                interval=Interval(start=now, end=now + timedelta(seconds=1)),
            )
            logger.info("received update from user", user=user, update=update)
            await self.core.send_calendar_updates(user, [update])

    async def update_user_list(self, users: list[User]) -> None:
        self.users = users

    async def send_free_busy(self, user: User, update: FreeBusy) -> None:
        logger.info("sent free-busy info to user", user=user, update=update)
