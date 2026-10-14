# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from collections.abc import AsyncIterator

import structlog

from ..model import Group, User
from ..ms_graph import MsGraph

logger = structlog.stdlib.get_logger()

# Only groups with this prefix are relevant to the sync, everything else is ignored.
GROUP_PREFIX = "msync-"


class GroupNotFoundError(Exception):
    pass


class MicrosoftCloudDirectory:
    _client: MsGraph

    def __init__(self, client: MsGraph) -> None:
        self._client = client

    def list_groups(self) -> AsyncIterator[Group]:
        return self._client.list_groups(GROUP_PREFIX)

    async def list_group_members(self, group: Group) -> AsyncIterator[User]:
        group_id = await self._resolve_group_name_to_id(group.name)
        if group_id is None:
            raise GroupNotFoundError(group.name)

        async for user in self._client.list_group_members(group_id):
            yield user

    async def _resolve_group_name_to_id(self, group_name: str) -> str | None:
        # TODO: to avoid an expensive query here, we should probably cache the
        #   name -> id mapping
        async for group in self.list_groups():
            if group.name.lower() == group_name:
                return group.id
