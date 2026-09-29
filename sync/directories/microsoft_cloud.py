# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from collections.abc import AsyncIterator
from typing import Protocol, Self, TypeVar

import structlog
from msgraph.graph_service_client import GraphServiceClient

from ..model import Group, User

logger = structlog.stdlib.get_logger()


T = TypeVar("T")


class GroupNotFoundError(Exception):
    pass


class _Page(Protocol[T]):
    value: list[T] | None
    odata_next_link: str | None


class _PaginatedRequestBuilder(Protocol[T]):
    async def get(self) -> _Page[T] | None: ...

    def with_url(self, raw_url: str) -> Self: ...


class MicrosoftCloudDirectory:
    _client: GraphServiceClient

    def __init__(self, client: GraphServiceClient) -> None:
        self._client = client

    async def list_groups(self) -> AsyncIterator[Group]:
        groups = (
            group
            async for page in self._paginate(self._client.groups)
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
        members = (member async for page in self._paginate(builder) for member in page)
        async for member in members:
            upn = getattr(member, "user_principal_name", None)
            assert member.id is not None
            if not isinstance(upn, str):
                logger.warning("user does not have a UPN", id=member.id)
                upn = ""
            yield User(name=upn, id=member.id)

    async def _paginate(
        self, builder: _PaginatedRequestBuilder[T]
    ) -> AsyncIterator[list[T]]:
        """Helper to traverse paginated queries."""

        while True:
            response = await builder.get()
            if response is None:
                return
            yield response.value or []
            next_link = response.odata_next_link
            if next_link is None:
                return
            builder = builder.with_url(next_link)

    async def _resolve_group_name_to_id(self, group_name: str) -> str | None:
        # TODO: to avoid an expensive query here, we should probably cache the
        #   name -> id mapping
        async for group in self.list_groups():
            if group.name.lower() == group_name:
                return group.id
