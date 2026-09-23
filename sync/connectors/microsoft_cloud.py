# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from collections.abc import AsyncIterator

from msgraph.graph_service_client import GraphServiceClient

from ..config import Settings
from ..model import Group, User

# TODO: what scope should we actually use??
_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"


class MicrosoftCloudConnector:
    _client: GraphServiceClient

    def __init__(self, settings: Settings) -> None:
        raise NotImplementedError

    def list_groups(self) -> AsyncIterator[Group]:
        raise NotImplementedError

    def list_group_members(self, group: Group) -> AsyncIterator[User]:
        raise NotImplementedError
