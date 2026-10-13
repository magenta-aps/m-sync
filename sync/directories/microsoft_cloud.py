# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from collections.abc import AsyncIterator

import structlog
from msgraph.graph_service_client import GraphServiceClient

from ..model import Group, User
from ..ms_graph import paginate

logger = structlog.stdlib.get_logger()


class GroupNotFoundError(Exception):
    pass


class MicrosoftCloudDirectory:
    _client: GraphServiceClient

    def __init__(self, client: GraphServiceClient) -> None:
        self._client = client

    async def list_groups(self) -> AsyncIterator[Group]:
        groups = (
            group
            async for page in paginate(self._client.groups)
            for group in page
            if group.display_name is not None
        )
        async for group in groups:
            assert group.display_name is not None
            yield Group(name=group.display_name, id=group.id)

    async def list_group_members(self, group: Group) -> AsyncIterator[User]:
        group_id = await self._resolve_group_name_to_id(group.name)
        if group_id is None:
            raise GroupNotFoundError(group.name)

        builder = self._client.groups.by_group_id(group_id).members
        members = (member async for page in paginate(builder) for member in page)
        async for member in members:
            upn = getattr(member, "user_principal_name", None)
            assert member.id is not None
            if not isinstance(upn, str):
                logger.warning("user does not have a UPN", id=member.id)
                upn = ""
            yield User(name=upn, id=member.id)

    async def _resolve_group_name_to_id(self, group_name: str) -> str | None:
        # TODO: to avoid an expensive query here, we should probably cache the
        #   name -> id mapping
        async for group in self.list_groups():
            if group.name.lower() == group_name:
                return group.id
