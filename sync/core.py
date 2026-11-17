# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


import asyncio
from dataclasses import dataclass
from datetime import datetime

import structlog
from pydantic import MISSING

from .model import (
    CalendarEvent,
    EventPatch,
    EventSource,
    GroupwareSystemName,
    User,
)
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

    async def full_sync(
        self,
        source: GroupwareSystemName,
        user: User,
        events: list[CalendarEvent],
        window_start: datetime,
        window_end: datetime,
    ) -> None:
        """Called by a connector when they perform a full sync. This lets core
        know what their authoritative list of events is, so that core can diff
        with other systems."""

        logger.info("requested full sync", user_id=user.id, source=source)

        if (mirror := await self._resolve_mirror(source)) is None:
            logger.warning("source with no corresponding mirror", source=source)
            return

        mirror_events = [
            event async for event in mirror.get_events(user, window_start, window_end)
        ]
        diff = _compute_diff(source, events, mirror_events)

        # avoid expensive calls on empty diffs
        if len(diff.created) != 0:
            await mirror.create_events(user, diff.created)
        if len(diff.deleted) != 0:
            await mirror.delete_events(user, diff.deleted)
        if len(diff.updated) != 0:
            await mirror.update_events(user, diff.updated)

    async def _resolve_mirror(
        self, source: GroupwareSystemName
    ) -> GroupwareConnector | None:
        # TODO: we don't currently support having multiple mirrors for the same
        #   source. This will arbitrarily pick the first one in that case.

        # find the first matching mirror system
        async for group in self._directory_service.list_groups():
            if (pair := group.system_names()) is None:
                continue
            if pair[0] != source:
                continue
            if (mirror := self._connectors.get(pair[1])) is None:
                continue
            return mirror
        return None


@dataclass(frozen=True)
class _EventDiff:
    created: list[CalendarEvent]
    deleted: list[EventSource]
    updated: list[EventPatch]


def _compute_diff(
    source_system: GroupwareSystemName,
    source: list[CalendarEvent],
    mirror: list[CalendarEvent],
) -> _EventDiff:

    created: list[CalendarEvent] = []
    deleted: list[EventSource] = []
    updated: list[EventPatch] = []

    # only manage events on the mirror that are owned by the source system.
    # Events owned by the mirror itself, or by other systems, should be unaffected
    mirror_events = {
        event.source: event
        for event in mirror
        if event.source is not None and event.source.system == source_system
    }

    for source_event in source:
        assert source_event.source is not None

        try:
            # by removing each event source and mirror have in common, we
            # retain only the events that are in mirror, but not in source
            mirror_event = mirror_events.pop(source_event.source)
        except KeyError:
            # mirror doesn't have the event, so we need to create it
            created.append(source_event)
            continue

        # source and mirror both have the event, so we diff the event contents
        # and build a patch containing only the fields that differ
        interval = (
            MISSING
            if mirror_event.interval == source_event.interval
            else source_event.interval
        )
        free_busy_status = (
            MISSING
            if mirror_event.free_busy_status == source_event.free_busy_status
            else source_event.free_busy_status
        )
        subject = (
            MISSING
            if mirror_event.subject == source_event.subject
            else source_event.subject
        )
        patch = EventPatch(
            source=source_event.source,
            interval=interval,
            free_busy_status=free_busy_status,
            subject=subject,
        )

        # avoid an expensive call on an empty diff
        if patch.is_noop():
            continue
        updated.append(patch)

    # whatever remains in mirror_events is stuff that is not in source so
    # it has to be deleted
    for mirror_event in mirror_events.values():
        assert mirror_event.source is not None
        deleted.append(mirror_event.source)

    return _EventDiff(created, deleted, updated)
