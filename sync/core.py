# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


import structlog

from .model import EventCreate, FreeBusy, GroupwareSystemName, UpdateBase, User
from .protocols import DirectoryServiceConnector, GroupwareConnector

logger = structlog.stdlib.get_logger()


class Core:
    _directory_service: DirectoryServiceConnector
    _connectors: dict[GroupwareSystemName, GroupwareConnector]

    def __init__(self, directory_service: DirectoryServiceConnector) -> None:
        self._directory_service = directory_service
        self._connectors = {}

    def add_groupware(self, *connectors: GroupwareConnector) -> None:
        for connector in connectors:
            system = connector.get_groupware_system_name()
            if system in self._connectors:
                raise ValueError(f"duplicate connector for {system!r}")
            self._connectors[system] = connector

    async def update_user_list(self) -> None:
        # TODO: put actual implementation here
        pass

    async def send_calendar_updates(
        self, user: User, updates: list[UpdateBase]
    ) -> None:
        """Called by a connector whenever their calendar changes"""

        logger.info("received updates", updates=updates)

        # TODO: hardcoded because no LDAP dummy
        target = self._connectors[GroupwareSystemName("beta")]

        for update in updates:
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
