# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from datetime import UTC, date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from more_itertools import one

from sync.config import Settings
from sync.model import (
    MICROSOFT_CLOUD,
    CalendarEvent,
    DatetimeInterval,
    EventSource,
    FreeBusyStatus,
    FullDayInterval,
    GroupwareSystemName,
    User,
)
from sync.ms_graph import DeltaLink, MsGraph, RealMsGraph

_AUCKLAND = ZoneInfo("Pacific/Auckland")
_CPH = ZoneInfo("Europe/Copenhagen")


async def _list_events(
    ms_graph: MsGraph,
    user_id: str,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, CalendarEvent]:
    events: dict[str, CalendarEvent] = {}
    async for event in ms_graph.list_calendar_events(user_id, window_start, window_end):
        if isinstance(event, DeltaLink):
            break
        event_id, calendar_event = event
        events[event_id] = calendar_event
    return events


async def test_auth(settings: Settings) -> None:
    """This just checks that a client is created succesfully."""

    _ = RealMsGraph(settings)


async def test_list_groups(ms_graph: MsGraph) -> None:
    groups = {group async for group in ms_graph.list_groups("msync-")}

    assert all(group.name.lower().startswith("msync-") for group in groups), groups
    assert any(
        group.name.lower() == "msync-microsoft_cloud-nextcloud" for group in groups
    )
    assert any(
        group.name.lower() == "msync-nextcloud-microsoft_cloud" for group in groups
    )


async def test_list_users(ms_graph: MsGraph) -> None:
    groups = {group async for group in ms_graph.list_groups("msync-")}

    group1 = one(
        group for group in groups if group.name == "msync-microsoft_cloud-nextcloud"
    )
    assert group1.id is not None
    user1 = one([user async for user in ms_graph.list_group_members(group1.id)])
    assert user1.name == "Laurits Testensen"
    assert user1.mail == "laurits-testensen@magentakommune.dk"

    group2 = one(
        group for group in groups if group.name == "msync-nextcloud-microsoft_cloud"
    )
    assert group2.id is not None
    user2 = one([user async for user in ms_graph.list_group_members(group2.id)])
    assert user2.name == "Anne Testensen"
    assert user2.mail == "anne-testensen@magentakommune.dk"


async def test_list_events_filters_by_time_window(
    ms_graph: MsGraph, ms_user: User
) -> None:
    """Tests that listing events only returns events within the given time window.

    Test data:
    ```
    time   t0    t1    t2    t3    t4    t5    t6    t7
    event1 |-----|     :                 :
    event2 |-----------|                 :
    event3 |-----------------|           :
    event4             :     |-----|     :
    event5             :           |-----------------|
    event6             :                 |-----------|
    event7             :                 :     |-----|
    window             |-----------------|
    ```

    The window overlaps with event3, event4 and event5, so we only expect to
    receive those.

    Event1 and Event7 are clearly not overlapping, so we expect to not receive
    them.

    Event2 and event6 are edge cases, as they are tangent to the window, so
    the test here is mostly just documenting the semantics.
    """

    t0 = datetime(year=2020, month=1, day=1, hour=0, tzinfo=UTC)
    t1 = datetime(year=2020, month=1, day=1, hour=1, tzinfo=UTC)
    t2 = datetime(year=2020, month=1, day=1, hour=2, tzinfo=UTC)
    t3 = datetime(year=2020, month=1, day=1, hour=3, tzinfo=UTC)
    t4 = datetime(year=2020, month=1, day=1, hour=4, tzinfo=UTC)
    t5 = datetime(year=2020, month=1, day=1, hour=5, tzinfo=UTC)
    t6 = datetime(year=2020, month=1, day=1, hour=6, tzinfo=UTC)
    t7 = datetime(year=2020, month=1, day=1, hour=7, tzinfo=UTC)
    source_system = GroupwareSystemName("test")

    async def make_event(start: datetime, end: datetime) -> str:
        event = CalendarEvent(
            source=EventSource(system=source_system, original_id=str(uuid4())),
            interval=DatetimeInterval(start=start, end=end),
            free_busy_status=FreeBusyStatus.BUSY,
        )
        return await ms_graph.create_event(ms_user.id, event)

    e1 = await make_event(t0, t1)
    e2 = await make_event(t1, t2)
    e3 = await make_event(t2, t3)
    e4 = await make_event(t3, t4)
    e5 = await make_event(t4, t5)
    e6 = await make_event(t5, t6)
    e7 = await make_event(t6, t7)

    events = await _list_events(ms_graph, ms_user.id, t2, t5)

    # these events should be returned by listing
    assert e2 in events
    assert e3 in events
    assert e4 in events
    assert e5 in events

    # these events should be filtered out by listing
    assert e1 not in events
    assert e6 not in events
    assert e7 not in events


async def test_create_then_delete_borrowed_event(
    ms_graph: MsGraph, ms_user: User
) -> None:
    """Tests that a borrowed event shows up in a listing with its original
    source intact, and that a deleted event no longer does.

    Deleting requires the event ID that Microsoft assigned, which is also
    returned by `create_event`."""

    t0 = datetime(year=2020, month=1, day=1, hour=0, tzinfo=UTC)
    t1 = datetime(year=2020, month=1, day=1, hour=1, tzinfo=UTC)

    source = EventSource(system=GroupwareSystemName("test"), original_id=str(uuid4()))
    event = CalendarEvent(
        source=source,
        interval=DatetimeInterval(start=t0, end=t1),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    event_id = await ms_graph.create_event(ms_user.id, event)

    events_before = await _list_events(ms_graph, ms_user.id, t0, t1)
    # the borrowed event is listed with its original source intact
    assert event_id in events_before
    assert events_before[event_id].source == source

    await ms_graph.delete_event(ms_user.id, event_id)

    events_after = await _list_events(ms_graph, ms_user.id, t0, t1)
    assert event_id not in events_after


async def test_full_day_event_round_trips(ms_graph: MsGraph, ms_user: User) -> None:
    """Tests that a full-day event round-trips with its dates intact.

    A full-day event ignores timezones, so even though Graph may return the
    times in another timezone representation than the one we sent, the dates
    of the interval must be exactly the ones we asked for."""

    interval = FullDayInterval(start=date(2026, 2, 9), end=date(2026, 2, 10))

    user_id = ms_user.id
    event = CalendarEvent(
        source=EventSource(
            system=GroupwareSystemName("test"), original_id=str(uuid4())
        ),
        interval=interval,
        free_busy_status=FreeBusyStatus.BUSY,
    )
    event_id = await ms_graph.create_event(user_id, event)
    events = await _list_events(
        ms_graph,
        user_id,
        datetime(2000, 1, 1, tzinfo=UTC),
        datetime(2100, 1, 1, tzinfo=UTC),
    )
    assert event_id in events
    listed = events[event_id]

    assert listed.interval == interval


async def test_owned_event_is_listed_with_microsoft_id(
    ms_graph: MsGraph, ms_user: User
) -> None:
    """Tests that an event created without a source is owned by Microsoft, in
    which case the ID Microsoft assigns to the event serves as the original ID
    that other systems can use to refer to the event."""

    t0 = datetime(year=2020, month=1, day=1, hour=0, tzinfo=UTC)
    t1 = datetime(year=2020, month=1, day=1, hour=1, tzinfo=UTC)

    event = CalendarEvent(
        source=None,
        interval=DatetimeInterval(start=t0, end=t1),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    event_id = await ms_graph.create_event(ms_user.id, event)

    events_before = await _list_events(ms_graph, ms_user.id, t0, t1)
    # for an owned event, the Microsoft event ID doubles as the original ID
    # that other systems can use to refer to the event
    assert event_id in events_before
    assert events_before[event_id].source == EventSource(
        system=MICROSOFT_CLOUD, original_id=event_id
    )

    await ms_graph.delete_event(ms_user.id, event_id)

    events_after = await _list_events(ms_graph, ms_user.id, t0, t1)
    assert event_id not in events_after
