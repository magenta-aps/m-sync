# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

"""Busy blocks: the calendar objects we write to a ghost's calendar so its
free/busy shows another system's busy time. One busy block per source event, with no
subject, place or attendees. Here are their names, their content, and reading
them back."""

import hashlib
from datetime import UTC, date, datetime
from typing import cast

from icalendar import Calendar, Event
from icalendar.enums import STATUS, TRANSP

from ..model import FreeBusyStatus, UpdateSource
from .ical import PRODID, read_upper_text
from .spans import BusyPeriod

# Every busy block's name starts with this. Anything else is not ours.
_BUSY_BLOCK_PREFIX = "msync-busy-"

# Hex digits of the hash in a busy block's name: 128 bits.
_NAME_HASH_LENGTH = 32


def make_busy_block_name(source: UpdateSource) -> str:
    """The object name of the busy block for one source event. The source id is
    hashed because it can contain "/" and be longer than the 255 characters
    Nextcloud allows for an object name."""
    hash_input = f"{source.system}:{source.original_id}".encode()
    name_hash = hashlib.sha256(hash_input).hexdigest()[:_NAME_HASH_LENGTH]
    return f"{_BUSY_BLOCK_PREFIX}{name_hash}.ics"


def render_busy_block(
    busy_block_name: str,
    start: datetime,
    end: datetime,
    status: FreeBusyStatus,
    timestamp: datetime,
) -> bytes:
    """A busy block that says the time is taken, and nothing else. `timestamp` is
    the time of the source change, so the same update gives the same bytes."""
    is_tentative = status is FreeBusyStatus.TENTATIVE
    busy_status = STATUS.TENTATIVE if is_tentative else STATUS.CONFIRMED
    event = Event.new(
        # The UID must equal the name. Nextcloud purges a trashed object with
        # the same UID on PUT, which is what lets the same busy block come back.
        uid=busy_block_name.removesuffix(".ics"),
        # In UTC: a time in another timezone would need a VTIMEZONE.
        stamp=timestamp.astimezone(UTC),
        start=start.astimezone(UTC),
        end=end.astimezone(UTC),
        summary="Busy",
        transparency=TRANSP.OPAQUE,
        status=busy_status,
    )
    # Wrap the busy event in a calendar, which is what Nextcloud stores.
    calendar = Calendar()
    calendar.version = "2.0"
    calendar.prodid = PRODID
    calendar.add_component(event)
    return calendar.to_ical()


def read_busy_block(calendar: Calendar) -> BusyPeriod | None:
    """What a busy block we wrote says, or None if it is not one we can read."""
    try:
        events = calendar.events
        if len(events) != 1:
            return None
        event = events[0]
        start: date | datetime = event.start
        end: date | datetime = event.end
    except ValueError:
        # Includes a missing DTSTART or DTEND.
        return None
    # A date has no time of day and no timezone, so it is not a busy block of ours.
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return None
    # Without a timezone the instant is unknown; we always write UTC.
    if start.tzinfo is None or end.tzinfo is None:
        return None
    return BusyPeriod(
        start=start.astimezone(UTC),
        end=end.astimezone(UTC),
        status=(
            FreeBusyStatus.TENTATIVE
            if read_upper_text(event, "STATUS") == "TENTATIVE"
            else FreeBusyStatus.BUSY
        ),
    )


def read_busy_block_timestamp(calendar: Calendar) -> datetime | None:
    """The DTSTAMP of a busy block we wrote: the time of the update it shows."""
    try:
        events = calendar.events
        timestamp = cast(object, events[0].DTSTAMP) if len(events) == 1 else None
    except (ValueError, KeyError):
        return None
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        return None
    return timestamp.astimezone(UTC)
