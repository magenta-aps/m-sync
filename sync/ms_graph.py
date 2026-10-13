# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

"""Utilities for talking with Microsoft Cloud Services"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol, Self

from azure.identity.aio import ClientSecretCredential
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.graph_service_client import GraphServiceClient

from .config import Settings


@dataclass(frozen=True)
class DeltaLink:
    url: str


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


def make_graph_client(settings: Settings) -> GraphServiceClient:
    """Connects and authenticates to Microsoft Cloud through its Graph API, returning
    a client object you can make queries through. This object automatically refreshes
    the connections and auth tokens."""

    assert settings.microsoft_cloud is not None, "missing Microsoft Cloud configuration"
    credentials = ClientSecretCredential(
        tenant_id=settings.microsoft_cloud.tenant_id,
        client_id=settings.microsoft_cloud.client_id,
        client_secret=settings.microsoft_cloud.client_secret.get_secret_value(),
    )
    return GraphServiceClient(credentials)
