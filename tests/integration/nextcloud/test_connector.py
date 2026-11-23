# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import asyncio
import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from more_itertools import one
from pydantic import SecretStr
from structlog.testing import capture_logs

from sync.config import NextcloudSettings
from sync.core import Core
from sync.groupware.nextcloud.client import NextcloudClient, RealNextcloudClient
from sync.groupware.nextcloud.connection import (
    NextcloudError,
    NotFoundError,
    quote_path_segment,
)
from sync.groupware.nextcloud.connector import NextcloudGroupware
from sync.groupware.nextcloud.dav import XML_CONTENT_TYPE, DavClient
from sync.groupware.nextcloud.ocs import NextcloudAccounts, OcsClient
from sync.model import (
    MICROSOFT_CLOUD,
    CalendarEvent,
    DatetimeInterval,
    EventPatch,
    EventSource,
    FreeBusyStatus,
    FullDayInterval,
    User,
)
from sync.nextcloud.ical import (
    make_busy_block_name,
    read_busy_block_timestamp,
    render_busy_block,
)
from sync.nextcloud.spans import BusyPeriod
from sync.protocols import GroupwareConnector
from tests.helpers import DummyDirectoryService, DummyGroupware

# A week far enough ahead that nothing else is booked then.
SUNDAY_MIDNIGHT = datetime(2030, 1, 6, tzinfo=UTC)
MONDAY_NINE = datetime(2030, 1, 7, 9, tzinfo=UTC)
MONDAY_TEN = datetime(2030, 1, 7, 10, tzinfo=UTC)
MONDAY_ELEVEN = datetime(2030, 1, 7, 11, tzinfo=UTC)
TUESDAY_MIDNIGHT = datetime(2030, 1, 8, tzinfo=UTC)

PLANNING = CalendarEvent(
    source=EventSource(system=MICROSOFT_CLOUD, original_id="planning"),
    interval=DatetimeInterval(start=MONDAY_NINE, end=MONDAY_TEN),
    free_busy_status=FreeBusyStatus.BUSY,
)
LUNCH = CalendarEvent(
    source=EventSource(system=MICROSOFT_CLOUD, original_id="lunch"),
    interval=DatetimeInterval(start=MONDAY_TEN, end=MONDAY_ELEVEN),
    free_busy_status=FreeBusyStatus.TENTATIVE,
)


@pytest.fixture
def connector(
    nextcloud_client: NextcloudClient, nextcloud_settings: NextcloudSettings
) -> GroupwareConnector:
    return NextcloudGroupware(nextcloud_client, nextcloud_settings)


@pytest.fixture
def mirrored_address() -> str:
    """The mail of `mirrored_user`."""
    return f"{uuid4()}@example.com"


@pytest.fixture
async def mirrored_user(
    cleanup_accounts: NextcloudAccounts, mirrored_address: str
) -> AsyncIterator[User]:
    """A user without an account yet. Its ghost is deleted after the test."""
    user_id = str(uuid4())
    yield User(id=user_id, name=f"upn-{user_id}@example.com", mail=mirrored_address)
    with suppress(NotFoundError):
        await cleanup_accounts.delete_user(user_id)


@pytest.fixture
async def taken_address(admin_ocs_client: OcsClient) -> AsyncIterator[str]:
    """The address of a new account outside the ghost group."""
    user_id = str(uuid4())
    address = f"{user_id}@example.com"
    user_path = f"/ocs/v2.php/cloud/users/{quote_path_segment(user_id)}"
    form = {"userid": user_id, "password": secrets.token_urlsafe()}
    try:
        _ = await admin_ocs_client.call_ocs(
            "POST", "/ocs/v2.php/cloud/users", object, form=form
        )
        # Set after the create, so Nextcloud sends no welcome mail.
        _ = await admin_ocs_client.call_ocs(
            "PUT", user_path, object, form={"key": "email", "value": address}
        )
        yield address
    finally:
        # A failed create can still leave the user behind.
        with suppress(NotFoundError):
            _ = await admin_ocs_client.call_ocs("DELETE", user_path, object)


async def _list_events(
    connector: GroupwareConnector,
    user: User,
    window_start: datetime,
    window_end: datetime,
) -> list[CalendarEvent]:
    return [
        event async for event in connector.get_events(user, window_start, window_end)
    ]


async def _share_read_only_as_ghost(
    settings: NextcloudSettings, ghost_id: str, ghost_password: SecretStr
) -> None:
    """Shares the ghost calendar again without `owncloud:read-write`."""
    body = (
        '<owncloud:share xmlns:dav="DAV:" xmlns:owncloud="http://owncloud.org/ns">'
        "<owncloud:set><dav:href>"
        f"principal:principals/users/{settings.service_user}"
        "</dav:href></owncloud:set></owncloud:share>"
    )
    ghost_auth = httpx.BasicAuth(ghost_id, ghost_password.get_secret_value())
    async with DavClient(settings.url, ghost_auth) as ghost_dav_client:
        response = await ghost_dav_client.request(
            "POST",
            f"/remote.php/dav/calendars/{ghost_id}/msync/",
            body,
            headers={"Content-Type": XML_CONTENT_TYPE},
        )
    assert response.status_code == 200


@pytest.fixture
def users_cannot_change_their_address() -> Iterator[None]:
    """Turns off `allow_user_to_change_email` with occ in the container named by
    NEXTCLOUD_CONTAINER and restores it after."""
    container = os.environ.get("NEXTCLOUD_CONTAINER")
    if not container:
        pytest.skip("no NEXTCLOUD_CONTAINER in .env")
    occ = ["docker", "exec", "-u", "www-data", container, "php", "occ"]
    setting = "allow_user_to_change_email"
    before = subprocess.run(
        [*occ, "config:system:get", setting],
        capture_output=True,
        text=True,
        check=False,
    )
    _ = subprocess.run(
        [*occ, "config:system:set", setting, "--type=boolean", "--value=false"],
        check=True,
    )
    try:
        yield
    finally:
        value = before.stdout.strip()
        restore = ["config:system:set", setting, "--type=boolean", f"--value={value}"]
        # A missing setting makes `get` fail, so it is deleted again.
        if before.returncode != 0:
            restore = ["config:system:delete", setting]
        _ = subprocess.run([*occ, *restore], check=True)


async def test_the_first_write_sets_up_a_disabled_ghost_with_the_busy_time(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mirrored_address: str,
) -> None:
    await connector.create_events(mirrored_user, [PLANNING])

    account = await nextcloud_client.accounts.get_account(mirrored_user.id)
    assert account.email == mirrored_user.mail
    assert not account.enabled
    assert (
        mirrored_user.id in await nextcloud_client.calendars.list_ghost_calendar_hrefs()
    )
    busy_periods = await nextcloud_client.principals.get_busy_periods_by_type(
        mirrored_address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert busy_periods == [BusyPeriod(start=MONDAY_NINE, end=MONDAY_TEN)]


async def test_the_ghost_is_named_after_the_user(
    connector: GroupwareConnector, admin_ocs_client: OcsClient, mirrored_user: User
) -> None:
    await connector.create_events(mirrored_user, [PLANNING])

    path = f"/ocs/v2.php/cloud/users/{quote_path_segment(mirrored_user.id)}"
    answer = await admin_ocs_client.call_ocs("GET", path, dict[str, object])
    assert answer.data["displayname"] == mirrored_user.name


async def test_a_ghost_of_a_user_without_a_name_is_named_after_the_mail(
    connector: GroupwareConnector, admin_ocs_client: OcsClient, mirrored_user: User
) -> None:
    user = mirrored_user.model_copy(update={"name": None})

    await connector.create_events(user, [PLANNING])

    path = f"/ocs/v2.php/cloud/users/{quote_path_segment(user.id)}"
    answer = await admin_ocs_client.call_ocs("GET", path, dict[str, object])
    assert answer.data["displayname"] == user.mail


async def test_a_later_write_goes_straight_to_the_ghost_calendar(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mirrored_address: str,
) -> None:
    await connector.create_events(mirrored_user, [PLANNING])
    # Setting the ghost up again would disable it.
    await nextcloud_client.accounts.set_enabled(mirrored_user.id, enabled=True)

    await connector.create_events(mirrored_user, [LUNCH])

    account = await nextcloud_client.accounts.get_account(mirrored_user.id)
    assert account.enabled
    busy_periods = await nextcloud_client.principals.get_busy_periods_by_type(
        mirrored_address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert busy_periods == [
        BusyPeriod(start=MONDAY_NINE, end=MONDAY_TEN),
        BusyPeriod(
            start=MONDAY_TEN, end=MONDAY_ELEVEN, status=FreeBusyStatus.TENTATIVE
        ),
    ]


async def test_a_moved_event_replaces_its_busy_block(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mirrored_address: str,
) -> None:
    moved_planning = PLANNING.model_copy(
        update={"interval": DatetimeInterval(start=MONDAY_TEN, end=MONDAY_ELEVEN)}
    )
    await connector.create_events(mirrored_user, [PLANNING])

    await connector.create_events(mirrored_user, [moved_planning])

    calendar_hrefs = await nextcloud_client.calendars.list_ghost_calendar_hrefs()
    objects = await nextcloud_client.calendars.list_objects(
        calendar_hrefs[mirrored_user.id], SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    busy_event = one(one(objects.values()).events)
    assert (busy_event.start, busy_event.end) == (MONDAY_TEN, MONDAY_ELEVEN)
    busy_periods = await nextcloud_client.principals.get_busy_periods_by_type(
        mirrored_address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert busy_periods == [BusyPeriod(start=MONDAY_TEN, end=MONDAY_ELEVEN)]


async def test_the_first_read_sets_up_a_disabled_ghost_with_no_events(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
) -> None:
    window_start = datetime(2030, 1, 6, tzinfo=UTC)
    window_end = datetime(2030, 5, 6, tzinfo=UTC)

    events = await _list_events(connector, mirrored_user, window_start, window_end)

    assert events == []
    account = await nextcloud_client.accounts.get_account(mirrored_user.id)
    assert account.email == mirrored_user.mail
    assert not account.enabled
    assert (
        mirrored_user.id in await nextcloud_client.calendars.list_ghost_calendar_hrefs()
    )


async def test_created_events_are_read_back_in_the_window(
    connector: GroupwareConnector, mirrored_user: User
) -> None:
    window_start = datetime(2030, 1, 6, tzinfo=UTC)
    window_end = datetime(2030, 1, 8, tzinfo=UTC)
    nine = datetime(2030, 1, 7, 9, tzinfo=UTC)
    ten = datetime(2030, 1, 7, 10, tzinfo=UTC)
    planning = CalendarEvent(
        source=EventSource(system=MICROSOFT_CLOUD, original_id="AAMkAD/planning+1="),
        interval=DatetimeInterval(start=nine, end=ten),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    lunch = CalendarEvent(
        source=EventSource(system=MICROSOFT_CLOUD, original_id="lunch"),
        interval=DatetimeInterval(start=ten, end=ten + timedelta(hours=1)),
        free_busy_status=FreeBusyStatus.TENTATIVE,
    )
    walk = CalendarEvent(
        source=EventSource(system=MICROSOFT_CLOUD, original_id="walk"),
        interval=DatetimeInterval(start=nine, end=ten),
        free_busy_status=FreeBusyStatus.FREE,
    )
    call = CalendarEvent(
        source=EventSource(system=MICROSOFT_CLOUD, original_id="call"),
        interval=DatetimeInterval(start=nine, end=ten),
        free_busy_status=FreeBusyStatus.UNKNOWN,
    )
    day_off = CalendarEvent(
        source=EventSource(system=MICROSOFT_CLOUD, original_id="day-off"),
        interval=FullDayInterval(start=date(2030, 1, 7), end=date(2030, 1, 8)),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    later = CalendarEvent(
        source=EventSource(system=MICROSOFT_CLOUD, original_id="later"),
        interval=DatetimeInterval(
            start=nine + timedelta(days=2), end=ten + timedelta(days=2)
        ),
        free_busy_status=FreeBusyStatus.BUSY,
    )

    await connector.create_events(
        mirrored_user, [planning, lunch, walk, call, day_off, later]
    )

    events = await _list_events(connector, mirrored_user, window_start, window_end)
    assert sorted(events, key=str) == sorted(
        [planning, lunch, walk, call, day_off], key=str
    )


async def test_an_event_created_again_replaces_its_busy_block(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
) -> None:
    window_start = datetime(2030, 1, 6, tzinfo=UTC)
    window_end = datetime(2030, 1, 8, tzinfo=UTC)
    nine = datetime(2030, 1, 7, 9, tzinfo=UTC)
    ten = datetime(2030, 1, 7, 10, tzinfo=UTC)
    planning = CalendarEvent(
        source=EventSource(system=MICROSOFT_CLOUD, original_id="planning"),
        interval=DatetimeInterval(start=nine, end=ten),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    moved_planning = planning.model_copy(
        update={"interval": DatetimeInterval(start=ten, end=ten + timedelta(hours=1))}
    )
    await connector.create_events(mirrored_user, [planning])
    # DTSTAMP has whole seconds.
    before_move = datetime.now(UTC).replace(microsecond=0)

    await connector.create_events(mirrored_user, [moved_planning])

    calendar_hrefs = await nextcloud_client.calendars.list_ghost_calendar_hrefs()
    objects = await nextcloud_client.calendars.list_objects(
        calendar_hrefs[mirrored_user.id], window_start, window_end
    )
    busy_block = one(objects.values())
    written_at = read_busy_block_timestamp(busy_block)
    assert written_at is not None
    assert written_at >= before_move
    events = await _list_events(connector, mirrored_user, window_start, window_end)
    assert events == [moved_planning]


async def test_deleted_events_are_gone_and_the_rest_stay(
    connector: GroupwareConnector, mirrored_user: User
) -> None:
    window_start = datetime(2030, 1, 6, tzinfo=UTC)
    window_end = datetime(2030, 1, 8, tzinfo=UTC)
    nine = datetime(2030, 1, 7, 9, tzinfo=UTC)
    ten = datetime(2030, 1, 7, 10, tzinfo=UTC)
    planning_source = EventSource(system=MICROSOFT_CLOUD, original_id="planning")
    cancelled_source = EventSource(system=MICROSOFT_CLOUD, original_id="cancelled")
    never_written_source = EventSource(system=MICROSOFT_CLOUD, original_id="never")
    planning = CalendarEvent(
        source=planning_source,
        interval=DatetimeInterval(start=nine, end=ten),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    cancelled = CalendarEvent(
        source=cancelled_source,
        interval=DatetimeInterval(start=ten, end=ten + timedelta(hours=1)),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    await connector.create_events(mirrored_user, [planning, cancelled])

    await connector.delete_events(
        mirrored_user, [cancelled_source, never_written_source]
    )

    events = await _list_events(connector, mirrored_user, window_start, window_end)
    assert events == [planning]


async def test_a_delete_for_a_user_without_a_ghost_is_logged(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
) -> None:
    planning_source = EventSource(system=MICROSOFT_CLOUD, original_id="planning")

    with capture_logs() as logs:
        await connector.delete_events(mirrored_user, [planning_source])

    log = one(
        log for log in logs if log["event"] == "cannot delete from ghost calendar"
    )
    assert log["user_id"] == mirrored_user.id
    with pytest.raises(NotFoundError):
        _ = await nextcloud_client.accounts.get_account(mirrored_user.id)


async def test_an_updated_event_keeps_what_the_patch_leaves_out(
    connector: GroupwareConnector, mirrored_user: User
) -> None:
    window_start = datetime(2030, 1, 6, tzinfo=UTC)
    window_end = datetime(2030, 1, 8, tzinfo=UTC)
    nine = datetime(2030, 1, 7, 9, tzinfo=UTC)
    ten = datetime(2030, 1, 7, 10, tzinfo=UTC)
    planning_source = EventSource(system=MICROSOFT_CLOUD, original_id="planning")
    lunch_source = EventSource(system=MICROSOFT_CLOUD, original_id="lunch")
    planning = CalendarEvent(
        source=planning_source,
        interval=DatetimeInterval(start=nine, end=ten),
        free_busy_status=FreeBusyStatus.TENTATIVE,
    )
    lunch = CalendarEvent(
        source=lunch_source,
        interval=DatetimeInterval(start=ten, end=ten + timedelta(hours=1)),
        free_busy_status=FreeBusyStatus.TENTATIVE,
    )
    moved_interval = DatetimeInterval(start=ten, end=ten + timedelta(hours=1))
    await connector.create_events(mirrored_user, [planning, lunch])

    await connector.update_events(
        mirrored_user,
        [
            EventPatch(source=planning_source, interval=moved_interval),
            EventPatch(source=lunch_source, free_busy_status=FreeBusyStatus.BUSY),
        ],
    )

    events = await _list_events(connector, mirrored_user, window_start, window_end)
    assert sorted(events, key=str) == sorted(
        [
            planning.model_copy(update={"interval": moved_interval}),
            lunch.model_copy(update={"free_busy_status": FreeBusyStatus.BUSY}),
        ],
        key=str,
    )


async def test_a_patch_with_only_a_subject_does_not_write_the_busy_block(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
) -> None:
    nine = datetime(2030, 1, 7, 9, tzinfo=UTC)
    ten = datetime(2030, 1, 7, 10, tzinfo=UTC)
    written_at = datetime(2030, 1, 1, tzinfo=UTC)
    planning_source = EventSource(system=MICROSOFT_CLOUD, original_id="planning")
    planning = CalendarEvent(
        source=planning_source,
        interval=DatetimeInterval(start=nine, end=ten),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    name = make_busy_block_name(planning_source)
    await connector.create_events(mirrored_user, [planning])
    calendar_hrefs = await nextcloud_client.calendars.list_ghost_calendar_hrefs()
    calendar_href = calendar_hrefs[mirrored_user.id]
    await nextcloud_client.calendars.put_object(
        calendar_href, name, render_busy_block(planning, written_at)
    )

    await connector.update_events(
        mirrored_user, [EventPatch(source=planning_source, subject="Planning")]
    )

    busy_block = await nextcloud_client.calendars.get_object(calendar_href, name)
    assert busy_block is not None
    assert read_busy_block_timestamp(busy_block) == written_at


async def test_an_update_through_a_read_only_share_is_logged_and_changes_nothing(
    nextcloud_settings: NextcloudSettings,
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    ghost_password: SecretStr,
) -> None:
    window_start = datetime(2030, 1, 6, tzinfo=UTC)
    window_end = datetime(2030, 1, 8, tzinfo=UTC)
    nine = datetime(2030, 1, 7, 9, tzinfo=UTC)
    ten = datetime(2030, 1, 7, 10, tzinfo=UTC)
    planning_source = EventSource(system=MICROSOFT_CLOUD, original_id="planning")
    planning = CalendarEvent(
        source=planning_source,
        interval=DatetimeInterval(start=nine, end=ten),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    await connector.create_events(mirrored_user, [planning])
    await nextcloud_client.accounts.set_password(mirrored_user.id, ghost_password)
    await nextcloud_client.accounts.set_enabled(mirrored_user.id, enabled=True)
    await _share_read_only_as_ghost(
        nextcloud_settings, mirrored_user.id, ghost_password
    )

    with capture_logs() as logs:
        await connector.update_events(
            mirrored_user,
            [EventPatch(source=planning_source, free_busy_status=FreeBusyStatus.FREE)],
        )

    log = one(log for log in logs if log["event"] == "cannot update in ghost calendar")
    assert log["user_id"] == mirrored_user.id
    events = await _list_events(connector, mirrored_user, window_start, window_end)
    assert events == [planning]


async def test_an_update_of_an_event_that_is_not_there_is_logged_and_skipped(
    connector: GroupwareConnector, mirrored_user: User
) -> None:
    window_start = datetime(2030, 1, 6, tzinfo=UTC)
    window_end = datetime(2030, 1, 8, tzinfo=UTC)
    nine = datetime(2030, 1, 7, 9, tzinfo=UTC)
    ten = datetime(2030, 1, 7, 10, tzinfo=UTC)
    planning = CalendarEvent(
        source=EventSource(system=MICROSOFT_CLOUD, original_id="planning"),
        interval=DatetimeInterval(start=nine, end=ten),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    never_written_source = EventSource(system=MICROSOFT_CLOUD, original_id="never")
    await connector.create_events(mirrored_user, [planning])

    with capture_logs() as logs:
        await connector.update_events(
            mirrored_user,
            [
                EventPatch(
                    source=never_written_source, free_busy_status=FreeBusyStatus.FREE
                )
            ],
        )

    assert {
        "event": "no readable busy block to update",
        "user_id": mirrored_user.id,
        "name": make_busy_block_name(never_written_source),
        "log_level": "warning",
    } in logs
    events = await _list_events(connector, mirrored_user, window_start, window_end)
    assert events == [planning]


async def test_full_syncs_through_core_make_the_ghost_calendar_follow_the_source(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
) -> None:
    window_start = datetime(2030, 1, 6, tzinfo=UTC)
    window_end = datetime(2030, 1, 9, tzinfo=UTC)
    nine = datetime(2030, 1, 7, 9, tzinfo=UTC)
    ten = datetime(2030, 1, 7, 10, tzinfo=UTC)
    directory = DummyDirectoryService()
    directory.group_ids["msync-microsoft_cloud-nextcloud"] = "mirrored-users"
    directory.group_members["mirrored-users"] = [mirrored_user]
    core = Core(directory, DummyGroupware(MICROSOFT_CLOUD), connector)
    planning = CalendarEvent(
        source=EventSource(system=MICROSOFT_CLOUD, original_id="planning"),
        interval=DatetimeInterval(start=nine, end=ten),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    cancelled = CalendarEvent(
        source=EventSource(system=MICROSOFT_CLOUD, original_id="cancelled"),
        interval=DatetimeInterval(start=ten, end=ten + timedelta(hours=1)),
        free_busy_status=FreeBusyStatus.TENTATIVE,
    )
    call = CalendarEvent(
        source=EventSource(system=MICROSOFT_CLOUD, original_id="call"),
        interval=DatetimeInterval(start=nine, end=ten),
        free_busy_status=FreeBusyStatus.UNKNOWN,
    )
    reminder = CalendarEvent(
        source=EventSource(system=MICROSOFT_CLOUD, original_id="reminder"),
        interval=DatetimeInterval(start=nine, end=nine),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    day_off = CalendarEvent(
        source=EventSource(system=MICROSOFT_CLOUD, original_id="day-off"),
        interval=FullDayInterval(start=date(2030, 1, 8), end=date(2030, 1, 9)),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    moved_planning = planning.model_copy(
        update={"interval": DatetimeInterval(start=ten, end=ten + timedelta(hours=1))}
    )
    later_events = [moved_planning, call, reminder, day_off]
    await core.full_sync(
        source=MICROSOFT_CLOUD,
        user=mirrored_user,
        events=[planning, cancelled, call, reminder, day_off],
        window_start=window_start,
        window_end=window_end,
    )

    await core.full_sync(
        source=MICROSOFT_CLOUD,
        user=mirrored_user,
        events=later_events,
        window_start=window_start,
        window_end=window_end,
    )

    events = await _list_events(connector, mirrored_user, window_start, window_end)
    assert sorted(events, key=str) == sorted(later_events, key=str)
    calendar_hrefs = await nextcloud_client.calendars.list_ghost_calendar_hrefs()
    calendar_href = calendar_hrefs[mirrored_user.id]
    busy_blocks = await nextcloud_client.calendars.list_objects(
        calendar_href, window_start, window_end
    )

    # DTSTAMP has whole seconds. A write after one second gets another DTSTAMP.
    await asyncio.sleep(1)
    await core.full_sync(
        source=MICROSOFT_CLOUD,
        user=mirrored_user,
        events=later_events,
        window_start=window_start,
        window_end=window_end,
    )

    # A full sync with nothing new writes nothing, so no busy block changes.
    assert busy_blocks == await nextcloud_client.calendars.list_objects(
        calendar_href, window_start, window_end
    )


async def test_a_read_skips_a_busy_block_under_another_name(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
) -> None:
    window_start = datetime(2030, 1, 6, tzinfo=UTC)
    window_end = datetime(2030, 1, 8, tzinfo=UTC)
    nine = datetime(2030, 1, 7, 9, tzinfo=UTC)
    ten = datetime(2030, 1, 7, 10, tzinfo=UTC)
    written_at = datetime(2030, 1, 1, tzinfo=UTC)
    planning = CalendarEvent(
        source=EventSource(system=MICROSOFT_CLOUD, original_id="planning"),
        interval=DatetimeInterval(start=nine, end=ten),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    lunch = CalendarEvent(
        source=EventSource(system=MICROSOFT_CLOUD, original_id="lunch"),
        interval=DatetimeInterval(start=ten, end=ten + timedelta(hours=1)),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    await connector.create_events(mirrored_user, [planning])
    calendar_hrefs = await nextcloud_client.calendars.list_ghost_calendar_hrefs()
    await nextcloud_client.calendars.put_object(
        calendar_hrefs[mirrored_user.id],
        "copied.ics",
        render_busy_block(lunch, written_at),
    )

    with capture_logs() as logs:
        events = await _list_events(connector, mirrored_user, window_start, window_end)

    assert events == [planning]
    assert {
        "event": "skipping busy block under another name",
        "user_id": mirrored_user.id,
        "name": "copied.ics",
        "log_level": "warning",
    } in logs


async def test_a_read_skips_a_busy_block_without_a_source(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
) -> None:
    window_start = datetime(2030, 1, 6, tzinfo=UTC)
    window_end = datetime(2030, 1, 8, tzinfo=UTC)
    nine = datetime(2030, 1, 7, 9, tzinfo=UTC)
    ten = datetime(2030, 1, 7, 10, tzinfo=UTC)
    written_at = datetime(2030, 1, 1, tzinfo=UTC)
    planning = CalendarEvent(
        source=EventSource(system=MICROSOFT_CLOUD, original_id="planning"),
        interval=DatetimeInterval(start=nine, end=ten),
        free_busy_status=FreeBusyStatus.BUSY,
    )
    busy_block = render_busy_block(planning, written_at)
    busy_event = one(busy_block.events)
    del busy_event["X-MSYNC-SOURCE-SYSTEM"]
    del busy_event["X-MSYNC-SOURCE-ORIGINAL-ID"]
    # The first read sets the ghost up.
    _ = await _list_events(connector, mirrored_user, window_start, window_end)
    calendar_hrefs = await nextcloud_client.calendars.list_ghost_calendar_hrefs()
    await nextcloud_client.calendars.put_object(
        calendar_hrefs[mirrored_user.id], "without-source.ics", busy_block
    )

    with capture_logs() as logs:
        events = await _list_events(connector, mirrored_user, window_start, window_end)

    assert events == []
    assert {
        "event": "skipping unreadable busy block",
        "user_id": mirrored_user.id,
        "name": "without-source.ics",
        "log_level": "warning",
    } in logs


async def test_a_read_for_a_user_without_mail_is_logged_and_has_no_events(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
) -> None:
    user = mirrored_user.model_copy(update={"mail": None})
    window_start = datetime(2030, 1, 6, tzinfo=UTC)
    window_end = datetime(2030, 5, 6, tzinfo=UTC)

    with capture_logs() as logs:
        events = await _list_events(connector, user, window_start, window_end)

    assert events == []
    assert {
        "event": "cannot set up ghost",
        "user_id": user.id,
        "reason": "no_mail",
        "log_level": "warning",
    } in logs
    with pytest.raises(NotFoundError):
        _ = await nextcloud_client.accounts.get_account(user.id)


@pytest.mark.parametrize(
    ("status", "shown_status"),
    [
        (FreeBusyStatus.TENTATIVE, FreeBusyStatus.TENTATIVE),
        (FreeBusyStatus.UNKNOWN, FreeBusyStatus.BUSY),
    ],
)
async def test_a_tentative_event_shows_as_tentative_and_an_unknown_one_as_busy(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mirrored_address: str,
    status: FreeBusyStatus,
    shown_status: FreeBusyStatus,
) -> None:
    event = PLANNING.model_copy(update={"free_busy_status": status})

    await connector.create_events(mirrored_user, [event])

    busy_periods = await nextcloud_client.principals.get_busy_periods_by_type(
        mirrored_address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert busy_periods == [
        BusyPeriod(start=MONDAY_NINE, end=MONDAY_TEN, status=shown_status)
    ]


async def test_a_free_event_is_written_transparent_and_leaves_the_time_free(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mirrored_address: str,
) -> None:
    free_planning = PLANNING.model_copy(
        update={"free_busy_status": FreeBusyStatus.FREE}
    )

    await connector.create_events(mirrored_user, [free_planning])

    calendar_hrefs = await nextcloud_client.calendars.list_ghost_calendar_hrefs()
    objects = await nextcloud_client.calendars.list_objects(
        calendar_hrefs[mirrored_user.id], SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    busy_event = one(one(objects.values()).events)
    assert busy_event["TRANSP"] == "TRANSPARENT"
    busy_periods = await nextcloud_client.principals.get_busy_periods_by_type(
        mirrored_address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert busy_periods == []


@pytest.mark.parametrize(
    ("calendar_timezone", "local_midnight"),
    [
        # An hour ahead of UTC in winter.
        ("Europe/Copenhagen", datetime(2030, 1, 6, 23, tzinfo=UTC)),
        ("UTC", datetime(2030, 1, 7, tzinfo=UTC)),
    ],
)
async def test_a_full_day_is_written_as_dates_and_covers_the_day_in_the_timezone(
    nextcloud_settings: NextcloudSettings,
    mirrored_user: User,
    mirrored_address: str,
    calendar_timezone: str,
    local_midnight: datetime,
) -> None:
    settings = nextcloud_settings.model_copy(
        update={"calendar_timezone": calendar_timezone}
    )
    day_off = PLANNING.model_copy(
        update={
            "interval": FullDayInterval(start=date(2030, 1, 7), end=date(2030, 1, 8))
        }
    )

    async with RealNextcloudClient(settings) as nextcloud:
        await NextcloudGroupware(nextcloud, settings).create_events(
            mirrored_user, [day_off]
        )

        calendar_hrefs = await nextcloud.calendars.list_ghost_calendar_hrefs()
        objects = await nextcloud.calendars.list_objects(
            calendar_hrefs[mirrored_user.id], SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
        )
        busy_periods = await nextcloud.principals.get_busy_periods_by_type(
            mirrored_address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
        )

    busy_event = one(one(objects.values()).events)
    assert not isinstance(busy_event.start, datetime)
    assert busy_event.start == date(2030, 1, 7)
    assert busy_event.end == date(2030, 1, 8)
    assert busy_periods == [
        BusyPeriod(start=local_midnight, end=local_midnight + timedelta(days=1))
    ]


async def test_an_event_that_takes_no_time_leaves_the_time_free(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mirrored_address: str,
) -> None:
    reminder = PLANNING.model_copy(
        update={"interval": DatetimeInterval(start=MONDAY_NINE, end=MONDAY_NINE)}
    )

    await connector.create_events(mirrored_user, [reminder])

    busy_periods = await nextcloud_client.principals.get_busy_periods_by_type(
        mirrored_address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert busy_periods == []


async def test_a_deleted_ghost_is_set_up_again_on_the_next_write(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mirrored_address: str,
) -> None:
    await connector.create_events(mirrored_user, [PLANNING])
    await nextcloud_client.accounts.delete_user(mirrored_user.id)

    await connector.create_events(mirrored_user, [LUNCH])

    busy_periods = await nextcloud_client.principals.get_busy_periods_by_type(
        mirrored_address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert busy_periods == [
        BusyPeriod(start=MONDAY_TEN, end=MONDAY_ELEVEN, status=FreeBusyStatus.TENTATIVE)
    ]


async def test_a_ghost_without_a_calendar_is_finished_on_the_first_write(
    nextcloud_client: NextcloudClient, connector: GroupwareConnector, ghost: str
) -> None:
    # The `ghost` fixture is enabled and has neither address nor calendar.
    address = f"{ghost}@example.com"
    user = User(id=ghost, name=f"upn-{ghost}@example.com", mail=address)

    await connector.create_events(user, [PLANNING])

    account = await nextcloud_client.accounts.get_account(ghost)
    assert account.email == address
    assert not account.enabled
    busy_periods = await nextcloud_client.principals.get_busy_periods_by_type(
        address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert busy_periods == [BusyPeriod(start=MONDAY_NINE, end=MONDAY_TEN)]


@pytest.mark.usefixtures("users_cannot_change_their_address")
async def test_the_manager_sets_the_address_when_users_cannot_change_their_own(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
) -> None:
    await connector.create_events(mirrored_user, [PLANNING])

    account = await nextcloud_client.accounts.get_account(mirrored_user.id)
    assert account.email == mirrored_user.mail
    assert not account.enabled


async def test_a_refused_address_releases_the_share_until_the_next_write(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mirrored_address: str,
) -> None:
    # It looks like an address, but Nextcloud refuses two dots in a row.
    refused_user = mirrored_user.model_copy(
        update={"mail": f"{mirrored_user.id}..a@example.com"}
    )

    with pytest.raises(NextcloudError):
        await connector.create_events(refused_user, [PLANNING])
    calendar_hrefs = await nextcloud_client.calendars.list_ghost_calendar_hrefs()
    await connector.create_events(mirrored_user, [PLANNING])

    assert mirrored_user.id not in calendar_hrefs
    account = await nextcloud_client.accounts.get_account(mirrored_user.id)
    assert account.email == mirrored_user.mail
    assert not account.enabled
    busy_periods = await nextcloud_client.principals.get_busy_periods_by_type(
        mirrored_address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert busy_periods == [BusyPeriod(start=MONDAY_NINE, end=MONDAY_TEN)]


async def test_an_account_with_an_address_keeps_its_password(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    ghost: str,
    ghost_password: SecretStr,
) -> None:
    # The `ghost` fixture has no calendar, so a write sets it up.
    other_address = f"other-{ghost}@example.com"
    await nextcloud_client.accounts.edit_user(ghost, "email", other_address)
    user = User(id=ghost, name=f"upn-{ghost}@example.com", mail=f"{ghost}@example.com")

    with capture_logs() as logs:
        await connector.create_events(user, [PLANNING])

    assert {
        "event": "cannot set up ghost",
        "user_id": ghost,
        "reason": "has_address",
        "log_level": "warning",
    } in logs
    account = await nextcloud_client.accounts.get_account(ghost)
    assert account.email == other_address
    # It still logs in with its old password.
    _ = await nextcloud_client.calendars.prepare_ghost_calendar(ghost, ghost_password)


async def test_an_event_of_a_user_with_a_real_account_is_skipped(
    nextcloud_settings: NextcloudSettings,
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
) -> None:
    # The service account is a real account outside the ghost group.
    service_user = nextcloud_settings.service_user
    user = User(id=service_user, name=service_user, mail=f"{uuid4()}@example.com")

    with capture_logs() as logs:
        await connector.create_events(user, [PLANNING])

    assert not await nextcloud_client.accounts.is_in_group(
        service_user, nextcloud_settings.ghost_group
    )
    assert {
        "event": "cannot set up ghost",
        "user_id": service_user,
        "reason": "real_account",
        "log_level": "warning",
    } in logs


async def test_an_admin_in_the_ghost_group_is_a_real_account(
    connector: GroupwareConnector, admin_in_ghost_group: str
) -> None:
    user = User(
        id=admin_in_ghost_group,
        name=admin_in_ghost_group,
        mail=f"{uuid4()}@example.com",
    )

    with capture_logs() as logs:
        await connector.create_events(user, [PLANNING])

    assert {
        "event": "cannot set up ghost",
        "user_id": admin_in_ghost_group,
        "reason": "real_account",
        "log_level": "warning",
    } in logs


async def test_an_address_already_in_use_is_not_given_to_a_ghost(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    taken_address: str,
) -> None:
    user = mirrored_user.model_copy(update={"mail": taken_address})

    with capture_logs() as logs:
        await connector.create_events(user, [PLANNING])

    assert {
        "event": "cannot set up ghost",
        "user_id": user.id,
        "reason": "address_taken",
        "log_level": "warning",
    } in logs
    with pytest.raises(NotFoundError):
        _ = await nextcloud_client.accounts.get_account(user.id)


@pytest.mark.parametrize("mail", [None, ""])
async def test_a_user_without_mail_gets_no_ghost(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mail: str | None,
) -> None:
    user = mirrored_user.model_copy(update={"mail": mail})

    with capture_logs() as logs:
        await connector.create_events(user, [PLANNING])

    assert {
        "event": "cannot set up ghost",
        "user_id": user.id,
        "reason": "no_mail",
        "log_level": "warning",
    } in logs
    with pytest.raises(NotFoundError):
        _ = await nextcloud_client.accounts.get_account(user.id)


@pytest.mark.parametrize(
    "mail",
    ["carl.example.com", "carl@localhost", "søren@example.com", "a b@example.com"],
)
async def test_a_mail_nextcloud_refuses_as_an_address_gets_no_ghost(
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mail: str,
) -> None:
    user = mirrored_user.model_copy(update={"mail": mail})

    with capture_logs() as logs:
        await connector.create_events(user, [PLANNING])

    assert {
        "event": "cannot set up ghost",
        "user_id": user.id,
        "reason": "not_an_address",
        "log_level": "warning",
    } in logs
    with pytest.raises(NotFoundError):
        _ = await nextcloud_client.accounts.get_account(user.id)


async def test_a_ghost_with_a_read_only_share_is_skipped_and_left_alone(
    nextcloud_settings: NextcloudSettings,
    nextcloud_client: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    ghost_password: SecretStr,
) -> None:
    await connector.create_events(mirrored_user, [PLANNING])
    await nextcloud_client.accounts.set_password(mirrored_user.id, ghost_password)
    await nextcloud_client.accounts.set_enabled(mirrored_user.id, enabled=True)
    await _share_read_only_as_ghost(
        nextcloud_settings, mirrored_user.id, ghost_password
    )

    # Setting it up again would set a password, which Nextcloud mails.
    with capture_logs() as logs:
        await connector.create_events(mirrored_user, [LUNCH])

    assert {
        "event": "cannot set up ghost",
        "user_id": mirrored_user.id,
        "reason": "has_address",
        "log_level": "warning",
    } in logs
    account = await nextcloud_client.accounts.get_account(mirrored_user.id)
    assert account.enabled
