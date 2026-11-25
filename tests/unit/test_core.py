# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from datetime import UTC, datetime

import pytest

from sync.core import Core
from sync.model import (
    CalendarEvent,
    DatetimeInterval,
    EventSource,
    FreeBusyStatus,
    GroupwareSystemName,
    User,
)
from tests.helpers import DummyDirectoryService, DummyGroupware


@pytest.fixture
def system_alpha() -> DummyGroupware:
    return DummyGroupware(GroupwareSystemName("alpha"))


@pytest.fixture
def system_beta() -> DummyGroupware:
    return DummyGroupware(GroupwareSystemName("beta"))


@pytest.fixture
def system_gamma() -> DummyGroupware:
    return DummyGroupware(GroupwareSystemName("gamma"))


@pytest.fixture
def directory() -> DummyDirectoryService:
    return DummyDirectoryService()


@pytest.fixture
def core(
    directory: DummyDirectoryService,
    system_alpha: DummyGroupware,
    system_beta: DummyGroupware,
    system_gamma: DummyGroupware,
) -> Core:
    return Core(directory, system_alpha, system_beta, system_gamma)


async def test_full_sync_no_events(
    directory: DummyDirectoryService,
    system_alpha: DummyGroupware,
    system_beta: DummyGroupware,
    core: Core,
):
    u1 = User(name="Alice", id="user-1")
    directory.group_ids["msync-alpha-beta"] = "group-1"
    directory.group_members["group-1"] = [u1]

    await core.full_sync(
        source=system_alpha.get_groupware_system_name(),
        user=u1,
        events=[],
        window_start=datetime(2020, 1, 1, tzinfo=UTC),
        window_end=datetime(2020, 1, 2, tzinfo=UTC),
    )

    assert len(system_beta.created_events) == 0
    assert len(system_beta.updated_events) == 0
    assert len(system_beta.deleted_events) == 0


async def test_full_sync_create_one(
    directory: DummyDirectoryService,
    system_alpha: DummyGroupware,
    system_beta: DummyGroupware,
    core: Core,
):
    t0 = datetime(2020, 1, 1, tzinfo=UTC)
    t1 = datetime(2020, 1, 2, tzinfo=UTC)
    t2 = datetime(2020, 1, 3, tzinfo=UTC)
    t3 = datetime(2020, 1, 4, tzinfo=UTC)
    u1 = User(name="Alice", id="user-1")
    directory.group_ids["msync-alpha-beta"] = "group-1"
    directory.group_members["group-1"] = [u1]
    e1 = CalendarEvent(
        source=EventSource(
            system=system_alpha.get_groupware_system_name(),
            original_id="event-1",
        ),
        interval=DatetimeInterval(start=t1, end=t2),
        free_busy_status=FreeBusyStatus.BUSY,
    )

    await core.full_sync(
        source=system_alpha.get_groupware_system_name(),
        user=u1,
        events=[e1],
        window_start=t0,
        window_end=t3,
    )

    assert system_beta.created_events[u1] == [e1]
    assert len(system_beta.updated_events) == 0
    assert len(system_beta.deleted_events) == 0


async def test_full_sync_delete_one(
    directory: DummyDirectoryService,
    system_alpha: DummyGroupware,
    system_beta: DummyGroupware,
    core: Core,
):
    t0 = datetime(2020, 1, 1, tzinfo=UTC)
    t1 = datetime(2020, 1, 2, tzinfo=UTC)
    t2 = datetime(2020, 1, 3, tzinfo=UTC)
    t3 = datetime(2020, 1, 4, tzinfo=UTC)
    u1 = User(name="Alice", id="user-1")
    directory.group_ids["msync-alpha-beta"] = "group-1"
    directory.group_members["group-1"] = [u1]
    e1 = CalendarEvent(
        source=EventSource(
            system=system_alpha.get_groupware_system_name(),
            original_id="event-1",
        ),
        interval=DatetimeInterval(start=t1, end=t2),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    system_beta.get_events_returns[u1] = [e1]

    await core.full_sync(
        source=system_alpha.get_groupware_system_name(),
        user=u1,
        events=[],
        window_start=t0,
        window_end=t3,
    )

    assert system_beta.deleted_events[u1] == [
        EventSource(
            system=system_alpha.get_groupware_system_name(),
            original_id="event-1",
        )
    ]
    assert len(system_beta.created_events) == 0
    assert len(system_beta.updated_events) == 0


async def test_full_sync_update_one(
    directory: DummyDirectoryService,
    system_alpha: DummyGroupware,
    system_beta: DummyGroupware,
    core: Core,
):
    t0 = datetime(2020, 1, 1, tzinfo=UTC)
    t1 = datetime(2020, 1, 2, tzinfo=UTC)
    t2 = datetime(2020, 1, 3, tzinfo=UTC)
    t3 = datetime(2020, 1, 4, tzinfo=UTC)
    u1 = User(name="Alice", id="user-1")
    directory.group_ids["msync-alpha-beta"] = "group-1"
    directory.group_members["group-1"] = [u1]
    event_source = EventSource(
        system=system_alpha.get_groupware_system_name(),
        original_id="event-1",
    )
    mirror_event = CalendarEvent(
        source=event_source,
        interval=DatetimeInterval(start=t1, end=t2),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    system_beta.get_events_returns[u1] = [mirror_event]
    source_event = CalendarEvent(
        source=event_source,
        interval=DatetimeInterval(start=t1, end=t3),
        free_busy_status=FreeBusyStatus.BUSY,
    )

    await core.full_sync(
        source=system_alpha.get_groupware_system_name(),
        user=u1,
        events=[source_event],
        window_start=t0,
        window_end=t3,
    )

    assert system_beta.updated_events[u1] == [source_event]
    assert len(system_beta.created_events) == 0
    assert len(system_beta.deleted_events) == 0


async def test_full_sync_other_user_not_affected(
    directory: DummyDirectoryService,
    system_alpha: DummyGroupware,
    system_beta: DummyGroupware,
    core: Core,
):
    t0 = datetime(2020, 1, 1, tzinfo=UTC)
    t1 = datetime(2020, 1, 2, tzinfo=UTC)
    t2 = datetime(2020, 1, 3, tzinfo=UTC)
    t3 = datetime(2020, 1, 4, tzinfo=UTC)

    u1 = User(name="Alice", id="user-1")
    u2 = User(name="Bob", id="user-2")
    directory.group_ids["msync-alpha-beta"] = "group-1"
    directory.group_members["group-1"] = [u1, u2]

    # user-1 owns event-1 on system_alpha
    e1 = CalendarEvent(
        source=EventSource(
            system=system_alpha.get_groupware_system_name(),
            original_id="event-1",
        ),
        interval=DatetimeInterval(start=t1, end=t2),
        free_busy_status=FreeBusyStatus.BUSY,
    )

    # user-2 owns event-2 on system_beta
    e2 = CalendarEvent(
        source=EventSource(
            system=system_alpha.get_groupware_system_name(),
            original_id="event-2",
        ),
        interval=DatetimeInterval(start=t1, end=t2),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    system_beta.get_events_returns[u2] = [e2]

    # we full-sync user-1 on system_alpha, so user-2's event-2 should be unchanged
    await core.full_sync(
        source=system_alpha.get_groupware_system_name(),
        user=u1,
        events=[e1],
        window_start=t0,
        window_end=t3,
    )

    # user-1's event is created in the mirror
    assert system_beta.created_events[u1] == [e1]

    # user-2 is untouched
    assert u2 not in system_beta.created_events
    assert u2 not in system_beta.updated_events
    assert u2 not in system_beta.deleted_events
    assert len(system_beta.updated_events) == 0
    assert len(system_beta.deleted_events) == 0


async def test_full_sync_outside_window_not_affected(
    directory: DummyDirectoryService,
    system_alpha: DummyGroupware,
    system_beta: DummyGroupware,
    core: Core,
):
    """
    Tests that an event outside of the window does not get synced by a full sync.

    ```
    time:           t0    t1    t2    t3    t4    t5
    source events:        |-----|           |-----|
    window:         |-----------------|
    mirror events:        |-----|
    ```
    """
    t0 = datetime(2020, 1, 1, tzinfo=UTC)
    t1 = datetime(2020, 1, 2, tzinfo=UTC)
    t2 = datetime(2020, 1, 3, tzinfo=UTC)
    t3 = datetime(2020, 1, 4, tzinfo=UTC)
    t4 = datetime(2020, 1, 5, tzinfo=UTC)
    t5 = datetime(2020, 1, 6, tzinfo=UTC)
    u1 = User(name="Alice", id="user-1")
    directory.group_ids["msync-alpha-beta"] = "group-1"
    directory.group_members["group-1"] = [u1]

    # an event inside the sync window, present only in the mirror
    e1 = CalendarEvent(
        source=EventSource(
            system=system_alpha.get_groupware_system_name(),
            original_id="event-1",
        ),
        interval=DatetimeInterval(start=t1, end=t2),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    # an event outside the sync window, present only in the mirror
    e2 = CalendarEvent(
        source=EventSource(
            system=system_alpha.get_groupware_system_name(),
            original_id="event-2",
        ),
        interval=DatetimeInterval(start=t4, end=t5),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    system_beta.get_events_returns[u1] = [e1, e2]

    await core.full_sync(
        source=system_alpha.get_groupware_system_name(),
        user=u1,
        events=[],
        window_start=t0,
        window_end=t3,
    )

    # the event inside the window is deleted, the one outside is untouched
    assert system_beta.deleted_events[u1] == [
        EventSource(
            system=system_alpha.get_groupware_system_name(),
            original_id="event-1",
        )
    ]
    assert len(system_beta.created_events) == 0
    assert len(system_beta.updated_events) == 0


async def test_full_sync_other_system_not_affected(
    directory: DummyDirectoryService,
    system_alpha: DummyGroupware,
    system_beta: DummyGroupware,
    system_gamma: DummyGroupware,
    core: Core,
):
    """
    Tests that a full sync only touches events on the mirror that are owned
    by the source system.
    """
    t0 = datetime(2020, 1, 1, tzinfo=UTC)
    t1 = datetime(2020, 1, 2, tzinfo=UTC)
    t2 = datetime(2020, 1, 3, tzinfo=UTC)
    t3 = datetime(2020, 1, 4, tzinfo=UTC)
    u1 = User(name="Alice", id="user-1")

    # note that it's not really intended for two sync pairs to have the same
    # mirror, but it is plausible that data from a prior configuration is still
    # present in the mirror calendar, so we still need to be cautious not to
    # delete data we don't own
    directory.group_ids["msync-alpha-beta"] = "group-1"
    directory.group_ids["msync-gamma-beta"] = "group-2"
    directory.group_members["group-1"] = [u1]
    directory.group_members["group-2"] = [u1]

    # an event on the mirror that is owned by system_gamma, which system_alpha
    # knows nothing about
    e1 = CalendarEvent(
        source=EventSource(
            system=system_gamma.get_groupware_system_name(),
            original_id="event-1",
        ),
        interval=DatetimeInterval(start=t1, end=t2),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    system_beta.get_events_returns[u1] = [e1]

    # system_alpha full-syncs, and has no events of its own
    await core.full_sync(
        source=system_alpha.get_groupware_system_name(),
        user=u1,
        events=[],
        window_start=t0,
        window_end=t3,
    )

    # the event owned by system_gamma is not deleted from the mirror
    assert u1 not in system_beta.deleted_events
    assert u1 not in system_beta.created_events
    assert u1 not in system_beta.updated_events
