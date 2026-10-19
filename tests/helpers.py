# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from collections.abc import AsyncIterator
from typing import override

import structlog

from sync.model import Group, User
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
