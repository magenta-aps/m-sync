# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


import structlog
from msgraph.graph_service_client import GraphServiceClient
from starlette.routing import Mount

from ...model import FreeBusy, GroupwareSystemName, User

logger = structlog.stdlib.get_logger()


class MicrosoftCloudGroupware:
    _client: GraphServiceClient

    def __init__(self, client: GraphServiceClient) -> None:
        self._client = client

    async def run(self) -> None:
        raise NotImplementedError

    def get_groupware_system_name(self) -> GroupwareSystemName:
        raise NotImplementedError

    def get_webhooks_mount(self) -> Mount:
        raise NotImplementedError

    async def update_user_list(self, users: list[User]) -> None:
        _ = users
        raise NotImplementedError

    async def send_free_busy(self, user: User, update: FreeBusy) -> None:
        _ = user
        _ = update
        raise NotImplementedError
