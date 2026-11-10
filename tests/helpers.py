# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import override

import structlog
from starlette.routing import Mount

from sync.model import FreeBusy, Group, GroupwareSystemName, User
from sync.ms_graph import MsGraph

logger = structlog.stdlib.get_logger()


class DummyDirectoryService:
    async def list_groups(self) -> AsyncIterator[Group]:
        return
        yield  # pyright: ignore[reportUnreachable]

    def list_group_members(
        self,
        group: Group,
    ) -> AsyncIterator[User]:
        _ = group
        raise NotImplementedError


@dataclass(frozen=True)
class DummyGroupware:
    name: GroupwareSystemName

    async def setup(self) -> None:
        """There is nothing to set up for the dummy."""

    async def run(self) -> None:
        raise NotImplementedError

    def get_groupware_system_name(self) -> GroupwareSystemName:
        return self.name

    def get_webhooks_mount(self) -> Mount | None:
        raise NotImplementedError

    async def update_user_list(self, users: set[User]) -> None:
        logger.info("received update user list", users=users)

    async def send_free_busy(self, user: User, update: FreeBusy) -> None:
        _ = user
        _ = update
        raise NotImplementedError


class DummyMsGraph(MsGraph):
    """Fake implementation of MsGraph for testing purposes."""

    # map name -> id
    group_ids: dict[str, str]

    # map id -> members
    group_members: dict[str, list[User]]

    def __init__(self):
        self.group_ids = {}
        self.group_members = {}

    @override
    async def list_groups(self, prefix: str) -> AsyncIterator[Group]:
        for k, v in self.group_ids.items():
            if not k.lower().startswith(prefix.lower()):
                continue
            yield Group(name=k, id=v)

    @override
    async def list_group_members(
        self,
        group_id: str,
    ) -> AsyncIterator[User]:
        members = self.group_members[group_id]
        for member in members:
            yield member
