# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol

from starlette.routing import Mount

from .model import (
    CalendarEvent,
    EventPatch,
    EventSource,
    FreeBusy,
    Group,
    GroupwareSystemName,
    User,
)


class GroupwareConnector(Protocol):
    """An adapter that connects an external groupware system like Nextcloud to
    the core"""

    async def setup(self) -> None:
        """Makes sure the connector is ready to receive requests, must be called
        before `Core` can forward requests to the connector."""

    async def run(self) -> None: ...

    def get_groupware_system_name(self) -> GroupwareSystemName: ...

    def get_webhooks_mount(self) -> Mount | None: ...

    def get_events(
        self,
        user: User,
        window_start: datetime,
        window_end: datetime,
    ) -> AsyncIterator[CalendarEvent]: ...

    async def update_user_list(self, users: set[User]) -> None: ...

    # TODO: remove this, use create_events or update_events instead
    async def send_free_busy(self, user: User, update: FreeBusy) -> None: ...

    async def create_events(self, user: User, events: list[CalendarEvent]) -> None: ...

    async def delete_events(self, user: User, events: list[EventSource]) -> None: ...

    async def update_events(self, user: User, patches: list[EventPatch]) -> None: ...


class DirectoryServiceConnector(Protocol):
    """An adapter for interfacing with directory services, like LDAP or Entra"""

    def list_groups(self) -> AsyncIterator[Group]: ...

    def list_group_members(self, group: Group) -> AsyncIterator[User]: ...
