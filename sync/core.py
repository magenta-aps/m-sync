# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


import asyncio

import structlog

from .model import GroupwareSystemName, User
from .protocols import DirectoryServiceConnector, GroupwareConnector

logger = structlog.stdlib.get_logger()


class Core:
    _directory_service: DirectoryServiceConnector
    _connectors: dict[GroupwareSystemName, GroupwareConnector]

    def __init__(
        self,
        directory_service: DirectoryServiceConnector,
        *groupware: GroupwareConnector,
    ) -> None:
        self._directory_service = directory_service
        self._connectors = {}
        self.add_groupware(*groupware)

    def add_groupware(self, *connectors: GroupwareConnector) -> None:
        for connector in connectors:
            system = connector.get_groupware_system_name()
            if system in self._connectors:
                raise ValueError(f"duplicate connector for {system!r}")
            self._connectors[system] = connector

    async def update_user_list(self) -> None:
        source_users: dict[GroupwareSystemName, set[User]] = {
            name: set() for name in self._connectors
        }

        async for group in self._directory_service.list_groups():
            pair = group.system_names()
            if pair is None:
                logger.info(
                    "ignoring group with non-matching name",
                    group_name=group.name,
                    group_id=group.id,
                )
                continue
            source, _ = pair
            if source not in source_users:
                logger.info(
                    "ignoring group for unknown source system",
                    source=source,
                    group_name=group.name,
                    group_id=group.id,
                )
                continue

            source_users[source] |= {
                member
                async for member in self._directory_service.list_group_members(group)
            }

        _ = await asyncio.gather(
            *(
                connector.update_user_list(source_users[key])
                for key, connector in self._connectors.items()
            )
        )
