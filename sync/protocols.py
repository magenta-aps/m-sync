# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from typing import Protocol

from .model import FreeBusy, GroupwareSystem, User


class GroupwareConnector(Protocol):
    """An adapter that connects an external groupware system like Nextcloud to
    the core"""

    def get_groupware_system(self) -> GroupwareSystem: ...

    async def update_user_list(self, ldap_users: list[User]) -> None: ...

    async def send_free_busy(self, user: User, update: FreeBusy) -> None: ...
