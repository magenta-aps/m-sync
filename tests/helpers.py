# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import override

import structlog
from starlette.routing import Mount

from sync.model import (
    CalendarEvent,
    EventPatch,
    EventSource,
    Group,
    GroupwareSystemName,
    User,
)
from sync.ms_graph import DeltaLink, MsGraph

logger = structlog.stdlib.get_logger()


class DummyDirectoryService:
    # map name -> id
    group_ids: dict[str, str]

    # map id -> members
    group_members: dict[str, list[User]]

    def __init__(self):
        self.group_ids = {}
        self.group_members = {}

    async def list_groups(self) -> AsyncIterator[Group]:
        for k, v in self.group_ids.items():
            yield Group(name=k, id=v)

    async def list_group_members(
        self,
        group: Group,
    ) -> AsyncIterator[User]:
        assert group.id is not None
        members = self.group_members[group.id]
        for member in members:
            yield member


@dataclass(frozen=True)
class DummyGroupware:
    name: GroupwareSystemName
    get_events_returns: dict[User, list[CalendarEvent]] = field(default_factory=dict)
    created_events: dict[User, list[CalendarEvent]] = field(default_factory=dict)
    deleted_events: dict[User, list[EventSource]] = field(default_factory=dict)
    updated_events: dict[User, list[EventPatch]] = field(default_factory=dict)

    async def setup(self) -> None:
        """There is nothing to set up for the dummy."""

    async def run(self) -> None:
        raise NotImplementedError

    def get_groupware_system_name(self) -> GroupwareSystemName:
        return self.name

    def get_webhooks_mount(self) -> Mount | None:
        raise NotImplementedError

    async def get_events(
        self,
        user: User,
        window_start: datetime,
        window_end: datetime,
    ) -> AsyncIterator[CalendarEvent]:
        events = self.get_events_returns.get(user)
        if events is None:
            return
        for event in events:
            # like a real connector querying its backing system, only yield
            # events that overlap the sync window
            start = event.interval.start_datetime()
            end = event.interval.end_datetime()
            if start < window_end and end > window_start:
                yield event

    async def update_user_list(self, users: set[User]) -> None:
        logger.info("received update user list", users=users)

    async def create_events(self, user: User, events: list[CalendarEvent]) -> None:
        if user not in self.created_events:
            self.created_events[user] = events
        else:
            self.created_events[user].extend(events)

    async def delete_events(self, user: User, events: list[EventSource]) -> None:
        if user not in self.deleted_events:
            self.deleted_events[user] = events
        else:
            self.deleted_events[user].extend(events)

    async def update_events(self, user: User, patches: list[EventPatch]) -> None:
        if user not in self.updated_events:
            self.updated_events[user] = patches
        else:
            self.updated_events[user].extend(patches)


class DummyMsGraph(MsGraph):
    """Fake implementation of MsGraph for testing purposes."""

    # map name -> id
    group_ids: dict[str, str]

    # map id -> members
    group_members: dict[str, list[User]]

    def __init__(self):
        self.group_ids = {}
        self.group_members = {}

    @override
    async def list_groups(self, prefix: str) -> AsyncIterator[Group]:
        for k, v in self.group_ids.items():
            if not k.lower().startswith(prefix.lower()):
                continue
            yield Group(name=k, id=v)

    @override
    async def list_group_members(
        self,
        group_id: str,
    ) -> AsyncIterator[User]:
        members = self.group_members[group_id]
        for member in members:
            yield member

    @override
    def list_calendar_events(
        self,
        user_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> AsyncIterator[tuple[str, CalendarEvent] | DeltaLink]:
        raise NotImplementedError()

    @override
    async def create_event(
        self,
        user_id: str,
        event: CalendarEvent,
    ) -> str:
        raise NotImplementedError()

    @override
    async def delete_event(self, user_id: str, event_id: str) -> None:
        raise NotImplementedError()
