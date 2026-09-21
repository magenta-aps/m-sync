# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


import asyncio

import structlog

from .model import EventCreate, FreeBusy, GroupwareSystemName, UpdateBase, User
from .protocols import GroupwareConnector

logger = structlog.stdlib.get_logger()


class Core:
    _connectors: dict[GroupwareSystemName, GroupwareConnector]

    def __init__(self) -> None:
        self._connectors = {}

    def add_connectors(self, *connectors: GroupwareConnector) -> None:
        for connector in connectors:
            system = connector.get_groupware_system_name()
            if system in self._connectors:
                raise ValueError(f"duplicate connector for {system!r}")
            self._connectors[system] = connector

    async def refresh_user_list(self) -> None:
        dummy_users = [
            User(id="foo@kommune.dk"),
            User(id="bar@kommune.dk"),
            User(id="baz@kommune.dk"),
        ]
        _ = await asyncio.gather(
            *(
                connector.update_user_list(dummy_users)
                for connector in self._connectors.values()
            )
        )

    async def send_calendar_update(self, user: User, update: UpdateBase) -> None:
        """Called by a connector whenever their calendar changes"""

        logger.info("received update", update=update)

        # TODO: hardcoded because no LDAP dummy
        target = self._connectors[GroupwareSystemName("beta")]

        match update:
            case EventCreate(
                source=source,
                timestamp=timestamp,
                interval=interval,
                free_busy_status=free_busy,
            ):
                await target.send_free_busy(
                    user,
                    FreeBusy(
                        source=source,
                        timestamp=timestamp,
                        interval=interval,
                        free_busy_status=free_busy,
                    ),
                )
            case _:
                raise NotImplementedError("TODO")

        # TODO: actually implement
