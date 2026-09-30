# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

"""Utilities for talking with Microsoft Cloud Services"""

from azure.identity.aio import ClientSecretCredential
from msgraph.graph_service_client import GraphServiceClient

from .config import Settings


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
