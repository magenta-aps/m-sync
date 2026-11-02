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
from msgraph.generated.models.event import (  # pyright: ignore[reportMissingTypeStubs]
    Event as _GraphEvent,
)
from msgraph.generated.models.free_busy_status import (  # pyright: ignore[reportMissingTypeStubs]
    FreeBusyStatus as _GraphFreeBusyStatus,
)
from msgraph.generated.models.open_type_extension import (  # pyright: ignore[reportMissingTypeStubs]
    OpenTypeExtension,
)
from msgraph.graph_service_client import GraphServiceClient

from sync.ms_graph.time import interval_to_graph_interval

from ..config import Settings
from ..model import CalendarEvent, FreeBusyStatus, Group, User
from ._base import DeltaLink, MsGraph

logger = structlog.stdlib.get_logger()

_EXTENSION_NAME = "dk.magenta.msync"


class MsGraphMisbehavingError(Exception):
    pass


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

    @override
    async def create_event(
        self,
        user_id: str,
        event: CalendarEvent,
    ) -> str:
        """Creates an event on the user's calendar.

        If the event's `source` field is set, it will be added to the Microsoft
        calendar as extension data. This lets you distinguish between an event
        owned by Microsoft and one that is borrowed."""

        interval = interval_to_graph_interval(event.interval)
        graph_event = _GraphEvent(
            subject=event.subject,
            is_all_day=interval.is_all_day,
            show_as=_msync_free_busy_to_graph_free_busy(event.free_busy_status),
            start=interval.start,
            end=interval.end,
            extensions=[
                OpenTypeExtension(
                    extension_name=_EXTENSION_NAME,
                    # XXX: there are undocumented restrictions on what data types
                    #   are allowed here, make sure you test before changing.
                    #   - Nested dictionaries are not allowed.
                    #   - Keys must only contain alphanumeric characters and underscores.
                    additional_data={
                        "source__system": str(event.source.system),
                        "source__original_id": event.source.original_id,
                    },
                )
            ]
            if event.source is not None
            else None,
        )
        event_handle = await self._sdk_client.users.by_user_id(user_id).events.post(
            graph_event
        )

        # these edge cases should never happen in practice
        if event_handle is None:
            raise MsGraphMisbehavingError("graph did not return an event handle")
        if event_handle.id is None:
            raise MsGraphMisbehavingError(
                "graph returned an event handle, but the ID is None"
            )

        return event_handle.id


def _msync_free_busy_to_graph_free_busy(status: FreeBusyStatus) -> _GraphFreeBusyStatus:
    match status:
        case FreeBusyStatus.FREE:
            return _GraphFreeBusyStatus.Free
        case FreeBusyStatus.BUSY:
            return _GraphFreeBusyStatus.Busy
        case FreeBusyStatus.TENTATIVE:
            return _GraphFreeBusyStatus.Tentative
        case FreeBusyStatus.UNKNOWN:
            return _GraphFreeBusyStatus.Unknown
