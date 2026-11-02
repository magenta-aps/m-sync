# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import override

from ..model import CalendarEvent, Group, User


@dataclass(frozen=True)
class DeltaLink:
    url: str

    @override
    def __str__(self) -> str:
        return self.url


class MsGraph(ABC):
    @abstractmethod
    def list_groups(self, prefix: str) -> AsyncIterator[Group]: ...

    @abstractmethod
    def list_group_members(self, group_id: str) -> AsyncIterator[User]: ...

    @abstractmethod
    async def create_event(
        self,
        user_id: str,
        event: CalendarEvent,
    ) -> str: ...

    @abstractmethod
    def list_calendar_events(
        self,
        user_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> AsyncIterator[tuple[str, CalendarEvent] | DeltaLink]: ...

    @abstractmethod
    async def delete_event(self, user_id: str, event_id: str) -> None: ...
