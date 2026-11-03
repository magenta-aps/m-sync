# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

"""Utilities for talking with Microsoft Cloud Services"""

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, Self, override

import structlog
from azure.identity.aio import ClientSecretCredential
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.groups.groups_request_builder import (  # pyright: ignore[reportMissingTypeStubs]
    GroupsRequestBuilder,
)
from msgraph.graph_service_client import GraphServiceClient

from ..config import Settings
from ..model import Group, User
from ._base import DeltaLink, MsGraph

logger = structlog.stdlib.get_logger()


class _Page[T](Protocol):
    """One page of a paginated query response."""

    @property
    def value(self) -> Sequence[T] | None: ...

    @property
    def odata_next_link(self) -> str | None: ...


class PaginatedRequestBuilder[T, Q](Protocol):
    async def get(
        self, request_configuration: RequestConfiguration[Q] | None = None
    ) -> _Page[T] | None: ...

    def with_url(self, raw_url: str) -> Self: ...


async def paginate[T, Q](
    builder: PaginatedRequestBuilder[T, Q],
    request_configuration: RequestConfiguration[Q] | None = None,
) -> AsyncIterator[T | DeltaLink]:
    """Helper to traverse paginated queries.

    It yields a sequence of values, and may optionally yield a single final
    DeltaLink object, which can be used for change tracking on queries that
    support it."""

    while True:
        response = await builder.get(request_configuration)
        if response is None:
            return
        for value in response.value or []:
            yield value
        next_link = response.odata_next_link
        if next_link is None:
            # not all page types returned by Microsoft's SDK have this property,
            # so we check for it dynamically
            delta_link: str | None = getattr(response, "odata_delta_link", None)

            if delta_link is not None:
                yield DeltaLink(delta_link)
            return
        builder = builder.with_url(next_link)


class RealMsGraph(MsGraph):
    _sdk_client: GraphServiceClient

    def __init__(self, settings: Settings) -> None:
        """Connects and authenticates to Microsoft Cloud through its Graph API, returning
        a client object you can make queries through. This object automatically refreshes
        the connections and auth tokens."""

        assert settings.microsoft_cloud is not None, (
            "missing Microsoft Cloud configuration"
        )
        credentials = ClientSecretCredential(
            tenant_id=settings.microsoft_cloud.tenant_id,
            client_id=settings.microsoft_cloud.client_id,
            client_secret=settings.microsoft_cloud.client_secret.get_secret_value(),
        )
        self._sdk_client = GraphServiceClient(credentials)

    @override
    async def list_groups(self, prefix: str) -> AsyncIterator[Group]:
        escaped_prefix = prefix.replace("'", "''")
        query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters(
            filter=f"startsWith(displayName, '{escaped_prefix}')"
        )
        config = RequestConfiguration(query_parameters=query_params)
        groups = (
            group
            async for group in paginate(self._sdk_client.groups, config)
            if not isinstance(group, DeltaLink)
            if group.display_name is not None
        )
        async for group in groups:
            name = group.display_name
            assert name is not None
            name = name.lower()
            if not name.startswith(prefix):
                continue
            yield Group(name=name, id=group.id)

    @override
    async def list_group_members(self, group_id: str) -> AsyncIterator[User]:
        builder = self._sdk_client.groups.by_group_id(group_id).members
        members = (
            member
            async for member in paginate(builder)
            if not isinstance(member, DeltaLink)
        )
        async for member in members:
            upn = getattr(member, "user_principal_name", None)
            mail = getattr(member, "mail", None)
            assert member.id is not None
            if not isinstance(upn, str):
                logger.warning("user does not have a UPN", id=member.id)
                upn = ""
            yield User(
                name=upn,
                id=member.id,
                mail=mail if isinstance(mail, str) else None,
            )
