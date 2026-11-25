# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


import asyncio
import re
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

import structlog
from pydantic import MISSING, SecretStr
from starlette.routing import Mount

from ...config import NextcloudSettings
from ...model import (
    NEXTCLOUD,
    CalendarEvent,
    EventPatch,
    EventSource,
    GroupwareSystemName,
    User,
)
from ...nextcloud.ical import make_busy_block_name, read_busy_block, render_busy_block
from .calendars import get_ghost_calendar_href
from .client import NextcloudClient
from .connection import (
    CalendarMissingError,
    NextcloudError,
    NotEditableError,
    NotFoundError,
    UnknownAddressError,
    UserExistsError,
)
from .ocs import NextcloudAccount

logger = structlog.stdlib.get_logger()

# roughly what Nextcloud accepts as an address, so most refused ones cost no setup
_ADDRESS_PATTERN: Final = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

# how long a window the address probe asks free/busy for
_ADDRESS_PROBE_WINDOW: Final = timedelta(hours=1)

# Nextcloud's password policy may want every character class
_PASSWORD_SUFFIX: Final = "aA1!"

type _ConflictReason = Literal[
    "no_mail", "real_account", "has_address", "address_taken", "not_an_address"
]


class _GhostConflictError(Exception):
    reason: _ConflictReason

    def __init__(self, reason: _ConflictReason) -> None:
        super().__init__(reason)
        self.reason = reason


class NextcloudGroupware:
    """Writes the busy time of mirrored users into their ghosts and reads it
    back as events. Nextcloud is only a mirror, so no user events are read from
    it and nothing is stored."""

    _client: NextcloudClient
    _service_user_id: str
    _ghost_group: str

    def __init__(self, client: NextcloudClient, settings: NextcloudSettings) -> None:
        self._client = client
        self._service_user_id = settings.service_user
        self._ghost_group = settings.ghost_group

    async def setup(self) -> None:
        """Nothing to set up. A ghost is set up on its first read or write."""

    async def run(self) -> None:
        """Wait until cancelled and then close the client."""
        # FIXME: a task cancelled before it starts never closes the client.
        try:
            _ = await asyncio.Event().wait()
        finally:
            await self._client.close()

    def get_groupware_system_name(self) -> GroupwareSystemName:
        return NEXTCLOUD

    def get_webhooks_mount(self) -> Mount | None:
        return None

    async def get_events(
        self,
        user: User,
        window_start: datetime,
        window_end: datetime,
    ) -> AsyncIterator[CalendarEvent]:
        """The events in the user's ghost calendar that overlap the window. A
        ghost that does not exist yet is set up and has none."""
        calendar_href = get_ghost_calendar_href(self._service_user_id, user.id)
        try:
            busy_blocks = await self._client.calendars.list_objects(
                calendar_href, window_start, window_end
            )
        except CalendarMissingError:
            _ = await self._set_up_ghost_or_log(user)
            return
        for name, busy_block in busy_blocks.items():
            event = read_busy_block(busy_block)
            if event is None or event.source is None:
                logger.warning(
                    "skipping unreadable busy block", user_id=user.id, name=name
                )
                continue
            # a busy block under another name is not one we wrote
            if make_busy_block_name(event.source) != name:
                logger.warning(
                    "skipping busy block under another name", user_id=user.id, name=name
                )
                continue
            yield event

    async def update_user_list(self, users: set[User]) -> None:
        # only users Nextcloud is the source for
        _ = users

    async def create_events(self, user: User, events: list[CalendarEvent]) -> None:
        """Write each event as a busy block in the user's ghost calendar. A
        write that finds no calendar sets the ghost up and writes them all once
        more. The same event written again replaces its busy block."""
        calendar_href = get_ghost_calendar_href(self._service_user_id, user.id)
        try:
            await self._put_busy_blocks(calendar_href, events)
        except CalendarMissingError:
            if not await self._set_up_ghost_or_log(user):
                return
            await self._put_busy_blocks(calendar_href, events)

    async def delete_events(self, user: User, events: list[EventSource]) -> None:
        """Delete the busy block of each source event from the user's ghost
        calendar. A busy block that is already gone is left alone. A ghost
        calendar that cannot be written is logged."""
        calendar_href = get_ghost_calendar_href(self._service_user_id, user.id)
        try:
            for source in events:
                name = make_busy_block_name(source)
                await self._client.calendars.delete_object(calendar_href, name)
        except CalendarMissingError as error:
            logger.warning(
                "cannot delete from ghost calendar", user_id=user.id, error=str(error)
            )

    async def update_events(self, user: User, patches: list[EventPatch]) -> None:
        """Change the busy block of each patched event in the user's ghost
        calendar. A patch with only a subject is skipped, since a busy block
        shows none. A patch for a busy block that is not there or cannot be
        read is logged and skipped. A ghost calendar that cannot be written is
        logged and the rest of the patches are skipped."""
        calendar_href = get_ghost_calendar_href(self._service_user_id, user.id)
        try:
            for patch in patches:
                await self._update_busy_block(user, calendar_href, patch)
        except CalendarMissingError as error:
            logger.warning(
                "cannot update in ghost calendar", user_id=user.id, error=str(error)
            )

    async def _update_busy_block(
        self, user: User, calendar_href: str, patch: EventPatch
    ) -> None:
        if patch.interval is MISSING and patch.free_busy_status is MISSING:
            return
        name = make_busy_block_name(patch.source)
        # FIXME: a write to the same busy block between this read and the put
        # is lost until the next full sync
        busy_block = await self._client.calendars.get_object(calendar_href, name)
        event = None if busy_block is None else read_busy_block(busy_block)
        if event is None:
            logger.warning(
                "no readable busy block to update", user_id=user.id, name=name
            )
            return
        interval = event.interval if patch.interval is MISSING else patch.interval
        free_busy_status = (
            event.free_busy_status
            if patch.free_busy_status is MISSING
            else patch.free_busy_status
        )
        patched_event = CalendarEvent(
            source=event.source,
            interval=interval,
            free_busy_status=free_busy_status,
        )
        await self._put_busy_blocks(calendar_href, [patched_event])

    async def _put_busy_blocks(
        self, calendar_href: str, events: list[CalendarEvent]
    ) -> None:
        written_at = datetime.now(UTC)
        for event in events:
            name = make_busy_block_name(event.get_source())
            busy_block = render_busy_block(event, written_at)
            await self._client.calendars.put_object(calendar_href, name, busy_block)

    async def _set_up_ghost_or_log(self, user: User) -> bool:
        """Whether the user now has a ghost. A user who cannot get one is logged."""
        try:
            await _set_up_ghost(self._client, self._ghost_group, user)
        except _GhostConflictError as error:
            logger.warning("cannot set up ghost", user_id=user.id, reason=error.reason)
            return False
        return True


async def _set_up_ghost(client: NextcloudClient, ghost_group: str, user: User) -> None:
    """Give the user a disabled ghost with its address and our calendar at the
    href get_ghost_calendar_href computes."""
    # FIXME: two tasks that set up the same ghost at once can break each other,
    # since each sets its own password and disables the ghost when it is done
    address = _read_ghost_address(user)
    try:
        account = await client.accounts.get_account(user.id)
    except NotFoundError:
        # the manager only sees the ghost group, so this is no ghost of ours
        account = None
    reason = await _find_conflict_reason(client, user, account, address)
    if reason is not None:
        raise _GhostConflictError(reason)
    password = SecretStr(secrets.token_urlsafe(32) + _PASSWORD_SUFFIX)
    if account is None:
        await _create_ghost(client, ghost_group, user, address, password)
    try:
        if account is not None:
            # an account without an address gets no mail about its password
            await client.accounts.set_enabled(user.id, enabled=True)
            await client.accounts.set_password(user.id, password)
        await _give_ghost_its_calendar_and_address(client, user, address, password)
    finally:
        # an enabled ghost can log in and be found as a person
        await _disable_ghost(client, user)
    logger.info("ghost ready", user_id=user.id)


def _read_ghost_address(user: User) -> str:
    """The user's mail, if it looks like an address Nextcloud accepts."""
    if not user.mail:
        raise _GhostConflictError("no_mail")
    if not (user.mail.isascii() and _ADDRESS_PATTERN.fullmatch(user.mail)):
        raise _GhostConflictError("not_an_address")
    return user.mail


async def _find_conflict_reason(
    client: NextcloudClient,
    user: User,
    account: NextcloudAccount | None,
    address: str,
) -> _ConflictReason | None:
    # also an account in the ghost group we may not read, such as an admin
    if account is None and await client.principals.user_exists(user.id):
        return "real_account"
    # a new password would mail the address
    if account is not None and account.email:
        return "has_address"
    # free/busy finds an address only if exactly one account has it and the
    # service account may share with that account, so a hidden one counts as free
    probe_start = datetime.now(UTC)
    try:
        busy_intervals = await client.principals.get_busy_intervals(
            address, probe_start, probe_start + _ADDRESS_PROBE_WINDOW
        )
    except UnknownAddressError:
        return None
    return None if busy_intervals is None else "address_taken"


async def _give_ghost_its_calendar_and_address(
    client: NextcloudClient, user: User, address: str, password: SecretStr
) -> None:
    """Without an address the share is released, so the next read or write gets
    404 and sets the ghost up again."""
    try:
        _ = await client.calendars.prepare_ghost_calendar(user.id, password)
        # the address comes last, since Nextcloud mails a later password change
        await _set_ghost_address(client, user, address, password)
    except NextcloudError:
        # Nextcloud can share or set the address and still fail to answer
        if not await _has_address(client, user):
            await _release_ghost_calendar(client, user, password)
            raise


async def _set_ghost_address(
    client: NextcloudClient, user: User, address: str, password: SecretStr
) -> None:
    """As the ghost, so the edit counts against the ghost's own rate limit."""
    try:
        await client.accounts.set_own_email(user.id, password, address)
    except NotEditableError:
        # Nextcloud can forbid users to change their own address
        await client.accounts.edit_user(user.id, "email", address)


async def _release_ghost_calendar(
    client: NextcloudClient, user: User, password: SecretStr
) -> None:
    try:
        await client.calendars.release_ghost_calendar(user.id, password)
    except NextcloudError as error:
        logger.error("cannot release ghost calendar", user_id=user.id, error=str(error))


async def _disable_ghost(client: NextcloudClient, user: User) -> None:
    # logged and not raised, so a failure to disable hides no earlier error
    try:
        await client.accounts.set_enabled(user.id, enabled=False)
    except NextcloudError as error:
        logger.error("cannot disable ghost", user_id=user.id, error=str(error))


async def _has_address(client: NextcloudClient, user: User) -> bool:
    # the ghost had no address before, so any address is ours
    try:
        account = await client.accounts.get_account(user.id)
    except NextcloudError:
        return False
    return bool(account.email)


async def _create_ghost(
    client: NextcloudClient,
    ghost_group: str,
    user: User,
    address: str,
    password: SecretStr,
) -> None:
    # Nextcloud shows a ghost without a display name by its id
    display_name = user.name or address
    try:
        await client.accounts.create_user(user.id, display_name, password, ghost_group)
    except UserExistsError:
        # another task created it and is setting it up, so it is left alone
        logger.warning("ghost exists already", user_id=user.id)
        raise
    except NextcloudError:
        # Nextcloud can fail after making the account, so disable it if it is ours
        if await client.accounts.is_in_group(user.id, ghost_group):
            await client.accounts.set_enabled(user.id, enabled=False)
        raise
