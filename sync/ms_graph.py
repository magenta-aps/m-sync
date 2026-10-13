# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

"""Utilities for talking with Microsoft Cloud Services"""

from collections.abc import AsyncIterator
from typing import Protocol, Self, TypeVar

from azure.identity.aio import ClientSecretCredential
from msgraph.graph_service_client import GraphServiceClient

from .config import Settings

T = TypeVar("T")


class _Page(Protocol[T]):
    value: list[T] | None
    odata_next_link: str | None


class _PaginatedRequestBuilder(Protocol[T]):
    async def get(self) -> _Page[T] | None: ...

    def with_url(self, raw_url: str) -> Self: ...


async def paginate(builder: _PaginatedRequestBuilder[T]) -> AsyncIterator[list[T]]:
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
