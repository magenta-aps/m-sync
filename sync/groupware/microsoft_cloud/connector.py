# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


import structlog
from starlette.routing import Mount

from sync.ms_graph import MsGraph

from ...model import FreeBusy, GroupwareSystemName, User

logger = structlog.stdlib.get_logger()


class MicrosoftCloudGroupware:
    _client: MsGraph

    def __init__(self, client: MsGraph) -> None:
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
