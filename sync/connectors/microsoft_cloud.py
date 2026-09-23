# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from collections.abc import AsyncIterator

from azure.identity.aio import ClientSecretCredential
from msgraph.graph_service_client import GraphServiceClient

from ..config import Settings
from ..model import Group, User

# TODO: what scope should we actually use??
_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"


class MicrosoftCloudConnector:
    _client: GraphServiceClient

    def __init__(self, settings: Settings) -> None:
        assert settings.microsoft_cloud is not None
        credentials = ClientSecretCredential(
            tenant_id=settings.microsoft_cloud.tenant_id,
            client_id=settings.microsoft_cloud.client_id,
            client_secret=settings.microsoft_cloud.client_secret.get_secret_value(),
        )
        self._client = GraphServiceClient(credentials)

    def list_groups(self) -> AsyncIterator[Group]:
        raise NotImplementedError

    def list_group_members(self, group: Group) -> AsyncIterator[User]:
        raise NotImplementedError
