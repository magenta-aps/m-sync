# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

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
    DatetimeInterval,
    FreeBusy,
    FreeBusyStatus,
    FullDayInterval,
    UpdateSource,
    User,
)
from sync.nextcloud.spans import BusyPeriod
from sync.protocols import GroupwareConnector

# A week far enough ahead that nothing else is booked then.
SUNDAY_MIDNIGHT = datetime(2030, 1, 6, tzinfo=UTC)
MONDAY_NINE = datetime(2030, 1, 7, 9, tzinfo=UTC)
MONDAY_TEN = datetime(2030, 1, 7, 10, tzinfo=UTC)
MONDAY_ELEVEN = datetime(2030, 1, 7, 11, tzinfo=UTC)
TUESDAY_MIDNIGHT = datetime(2030, 1, 8, tzinfo=UTC)

PLANNING = FreeBusy(
    source=UpdateSource(system=MICROSOFT_CLOUD, original_id="planning"),
    timestamp=datetime(2029, 12, 1, tzinfo=UTC),
    interval=DatetimeInterval(start=MONDAY_NINE, end=MONDAY_TEN),
    free_busy_status=FreeBusyStatus.BUSY,
)
LUNCH = FreeBusy(
    source=UpdateSource(system=MICROSOFT_CLOUD, original_id="lunch"),
    timestamp=datetime(2029, 12, 1, tzinfo=UTC),
    interval=DatetimeInterval(start=MONDAY_TEN, end=MONDAY_ELEVEN),
    free_busy_status=FreeBusyStatus.TENTATIVE,
)


@pytest.fixture
def connector(
    nextcloud: NextcloudClient, nextcloud_settings: NextcloudSettings
) -> GroupwareConnector:
    return NextcloudGroupware(nextcloud, nextcloud_settings)


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
    nextcloud: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mirrored_address: str,
) -> None:
    await connector.send_free_busy(mirrored_user, PLANNING)

    account = await nextcloud.accounts.get_account(mirrored_user.id)
    assert account.email == mirrored_user.mail
    assert not account.enabled
    assert mirrored_user.id in await nextcloud.calendars.list_ghost_calendar_hrefs()
    busy_periods = await nextcloud.principals.get_busy_periods_by_type(
        mirrored_address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert busy_periods == [BusyPeriod(start=MONDAY_NINE, end=MONDAY_TEN)]


async def test_the_ghost_is_named_after_the_user(
    connector: GroupwareConnector, admin_ocs_client: OcsClient, mirrored_user: User
) -> None:
    await connector.send_free_busy(mirrored_user, PLANNING)

    path = f"/ocs/v2.php/cloud/users/{quote_path_segment(mirrored_user.id)}"
    answer = await admin_ocs_client.call_ocs("GET", path, dict[str, object])
    assert answer.data["displayname"] == mirrored_user.name


async def test_a_later_write_goes_straight_to_the_ghost_calendar(
    nextcloud: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mirrored_address: str,
) -> None:
    await connector.send_free_busy(mirrored_user, PLANNING)
    # Setting the ghost up again would disable it.
    await nextcloud.accounts.set_enabled(mirrored_user.id, enabled=True)

    await connector.send_free_busy(mirrored_user, LUNCH)

    account = await nextcloud.accounts.get_account(mirrored_user.id)
    assert account.enabled
    busy_periods = await nextcloud.principals.get_busy_periods_by_type(
        mirrored_address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert busy_periods == [
        BusyPeriod(start=MONDAY_NINE, end=MONDAY_TEN),
        BusyPeriod(
            start=MONDAY_TEN, end=MONDAY_ELEVEN, status=FreeBusyStatus.TENTATIVE
        ),
    ]


async def test_a_moved_event_replaces_its_busy_block(
    nextcloud: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mirrored_address: str,
) -> None:
    moved_planning = PLANNING.model_copy(
        update={"interval": DatetimeInterval(start=MONDAY_TEN, end=MONDAY_ELEVEN)}
    )
    await connector.send_free_busy(mirrored_user, PLANNING)

    await connector.send_free_busy(mirrored_user, moved_planning)

    calendar_hrefs = await nextcloud.calendars.list_ghost_calendar_hrefs()
    objects = await nextcloud.calendars.list_objects(
        calendar_hrefs[mirrored_user.id], SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    busy_event = one(one(objects.values()).events)
    assert (busy_event.start, busy_event.end) == (MONDAY_TEN, MONDAY_ELEVEN)
    busy_periods = await nextcloud.principals.get_busy_periods_by_type(
        mirrored_address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert busy_periods == [BusyPeriod(start=MONDAY_TEN, end=MONDAY_ELEVEN)]


@pytest.mark.parametrize(
    ("status", "shown_status"),
    [
        (FreeBusyStatus.TENTATIVE, FreeBusyStatus.TENTATIVE),
        (FreeBusyStatus.UNKNOWN, FreeBusyStatus.BUSY),
    ],
)
async def test_a_tentative_event_shows_as_tentative_and_an_unknown_one_as_busy(
    nextcloud: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mirrored_address: str,
    status: FreeBusyStatus,
    shown_status: FreeBusyStatus,
) -> None:
    update = PLANNING.model_copy(update={"free_busy_status": status})

    await connector.send_free_busy(mirrored_user, update)

    busy_periods = await nextcloud.principals.get_busy_periods_by_type(
        mirrored_address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert busy_periods == [
        BusyPeriod(start=MONDAY_NINE, end=MONDAY_TEN, status=shown_status)
    ]


async def test_a_free_event_is_written_transparent_and_leaves_the_time_free(
    nextcloud: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mirrored_address: str,
) -> None:
    free_planning = PLANNING.model_copy(
        update={"free_busy_status": FreeBusyStatus.FREE}
    )

    await connector.send_free_busy(mirrored_user, free_planning)

    calendar_hrefs = await nextcloud.calendars.list_ghost_calendar_hrefs()
    objects = await nextcloud.calendars.list_objects(
        calendar_hrefs[mirrored_user.id], SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    busy_event = one(one(objects.values()).events)
    assert busy_event["TRANSP"] == "TRANSPARENT"
    busy_periods = await nextcloud.principals.get_busy_periods_by_type(
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
        await NextcloudGroupware(nextcloud, settings).send_free_busy(
            mirrored_user, day_off
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
    nextcloud: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mirrored_address: str,
) -> None:
    reminder = PLANNING.model_copy(
        update={"interval": DatetimeInterval(start=MONDAY_NINE, end=MONDAY_NINE)}
    )

    await connector.send_free_busy(mirrored_user, reminder)

    busy_periods = await nextcloud.principals.get_busy_periods_by_type(
        mirrored_address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert busy_periods == []


async def test_a_deleted_ghost_is_set_up_again_on_the_next_write(
    nextcloud: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mirrored_address: str,
) -> None:
    await connector.send_free_busy(mirrored_user, PLANNING)
    await nextcloud.accounts.delete_user(mirrored_user.id)

    await connector.send_free_busy(mirrored_user, LUNCH)

    busy_periods = await nextcloud.principals.get_busy_periods_by_type(
        mirrored_address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert busy_periods == [
        BusyPeriod(start=MONDAY_TEN, end=MONDAY_ELEVEN, status=FreeBusyStatus.TENTATIVE)
    ]


async def test_a_ghost_without_a_calendar_is_finished_on_the_first_write(
    nextcloud: NextcloudClient, connector: GroupwareConnector, ghost: str
) -> None:
    # The `ghost` fixture is enabled and has neither address nor calendar.
    address = f"{ghost}@example.com"
    user = User(id=ghost, name=f"upn-{ghost}@example.com", mail=address)

    await connector.send_free_busy(user, PLANNING)

    account = await nextcloud.accounts.get_account(ghost)
    assert account.email == address
    assert not account.enabled
    busy_periods = await nextcloud.principals.get_busy_periods_by_type(
        address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert busy_periods == [BusyPeriod(start=MONDAY_NINE, end=MONDAY_TEN)]


@pytest.mark.usefixtures("users_cannot_change_their_address")
async def test_the_manager_sets_the_address_when_users_cannot_change_their_own(
    nextcloud: NextcloudClient, connector: GroupwareConnector, mirrored_user: User
) -> None:
    await connector.send_free_busy(mirrored_user, PLANNING)

    account = await nextcloud.accounts.get_account(mirrored_user.id)
    assert account.email == mirrored_user.mail
    assert not account.enabled


async def test_a_refused_address_releases_the_share_until_the_next_write(
    nextcloud: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mirrored_address: str,
) -> None:
    # It looks like an address, but Nextcloud refuses two dots in a row.
    refused_user = mirrored_user.model_copy(
        update={"mail": f"{mirrored_user.id}..a@example.com"}
    )

    with pytest.raises(NextcloudError):
        await connector.send_free_busy(refused_user, PLANNING)
    calendar_hrefs = await nextcloud.calendars.list_ghost_calendar_hrefs()
    await connector.send_free_busy(mirrored_user, PLANNING)

    assert mirrored_user.id not in calendar_hrefs
    account = await nextcloud.accounts.get_account(mirrored_user.id)
    assert account.email == mirrored_user.mail
    assert not account.enabled
    busy_periods = await nextcloud.principals.get_busy_periods_by_type(
        mirrored_address, SUNDAY_MIDNIGHT, TUESDAY_MIDNIGHT
    )
    assert busy_periods == [BusyPeriod(start=MONDAY_NINE, end=MONDAY_TEN)]


async def test_an_account_with_an_address_keeps_its_password(
    nextcloud: NextcloudClient,
    connector: GroupwareConnector,
    ghost: str,
    ghost_password: SecretStr,
) -> None:
    # The `ghost` fixture has no calendar, so a write sets it up.
    other_address = f"other-{ghost}@example.com"
    await nextcloud.accounts.edit_user(ghost, "email", other_address)
    user = User(id=ghost, name=f"upn-{ghost}@example.com", mail=f"{ghost}@example.com")

    with capture_logs() as logs:
        await connector.send_free_busy(user, PLANNING)

    assert {
        "event": "cannot set up ghost",
        "user_id": ghost,
        "reason": "has_address",
        "log_level": "warning",
    } in logs
    account = await nextcloud.accounts.get_account(ghost)
    assert account.email == other_address
    # It still logs in with its old password.
    _ = await nextcloud.calendars.prepare_ghost_calendar(ghost, ghost_password)


async def test_an_event_of_a_user_with_a_real_account_is_skipped(
    nextcloud_settings: NextcloudSettings,
    nextcloud: NextcloudClient,
    connector: GroupwareConnector,
) -> None:
    # The service account is a real account outside the ghost group.
    service_user = nextcloud_settings.service_user
    user = User(id=service_user, name=service_user, mail=f"{uuid4()}@example.com")

    with capture_logs() as logs:
        await connector.send_free_busy(user, PLANNING)

    assert not await nextcloud.accounts.is_in_group(
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
        await connector.send_free_busy(user, PLANNING)

    assert {
        "event": "cannot set up ghost",
        "user_id": admin_in_ghost_group,
        "reason": "real_account",
        "log_level": "warning",
    } in logs


async def test_an_address_already_in_use_is_not_given_to_a_ghost(
    nextcloud: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    taken_address: str,
) -> None:
    user = mirrored_user.model_copy(update={"mail": taken_address})

    with capture_logs() as logs:
        await connector.send_free_busy(user, PLANNING)

    assert {
        "event": "cannot set up ghost",
        "user_id": user.id,
        "reason": "address_taken",
        "log_level": "warning",
    } in logs
    with pytest.raises(NotFoundError):
        _ = await nextcloud.accounts.get_account(user.id)


@pytest.mark.parametrize("mail", [None, ""])
async def test_a_user_without_mail_gets_no_ghost(
    nextcloud: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mail: str | None,
) -> None:
    user = mirrored_user.model_copy(update={"mail": mail})

    with capture_logs() as logs:
        await connector.send_free_busy(user, PLANNING)

    assert {
        "event": "cannot set up ghost",
        "user_id": user.id,
        "reason": "no_mail",
        "log_level": "warning",
    } in logs
    with pytest.raises(NotFoundError):
        _ = await nextcloud.accounts.get_account(user.id)


@pytest.mark.parametrize(
    "mail",
    ["carl.example.com", "carl@localhost", "søren@example.com", "a b@example.com"],
)
async def test_a_mail_nextcloud_refuses_as_an_address_gets_no_ghost(
    nextcloud: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    mail: str,
) -> None:
    user = mirrored_user.model_copy(update={"mail": mail})

    with capture_logs() as logs:
        await connector.send_free_busy(user, PLANNING)

    assert {
        "event": "cannot set up ghost",
        "user_id": user.id,
        "reason": "not_an_address",
        "log_level": "warning",
    } in logs
    with pytest.raises(NotFoundError):
        _ = await nextcloud.accounts.get_account(user.id)


async def test_a_ghost_with_a_read_only_share_is_skipped_and_left_alone(
    nextcloud_settings: NextcloudSettings,
    nextcloud: NextcloudClient,
    connector: GroupwareConnector,
    mirrored_user: User,
    ghost_password: SecretStr,
) -> None:
    await connector.send_free_busy(mirrored_user, PLANNING)
    await nextcloud.accounts.set_password(mirrored_user.id, ghost_password)
    await nextcloud.accounts.set_enabled(mirrored_user.id, enabled=True)
    await _share_read_only_as_ghost(
        nextcloud_settings, mirrored_user.id, ghost_password
    )

    # Setting it up again would set a password, which Nextcloud mails.
    with capture_logs() as logs:
        await connector.send_free_busy(mirrored_user, LUNCH)

    assert {
        "event": "cannot set up ghost",
        "user_id": mirrored_user.id,
        "reason": "has_address",
        "log_level": "warning",
    } in logs
    account = await nextcloud.accounts.get_account(mirrored_user.id)
    assert account.enabled
