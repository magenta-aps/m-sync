# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import re
from abc import ABC, abstractmethod
from datetime import date, datetime, time
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, NewType, Self, TypedDict

import starlette.requests
from pydantic import MISSING, UUID4, BaseModel, ConfigDict, Field
from pydantic.functional_validators import model_validator
from sqlalchemy.ext.asyncio import AsyncEngine
from typing_extensions import override

GroupwareSystemName = NewType("GroupwareSystemName", str)


MICROSOFT_CLOUD = GroupwareSystemName("microsoft_cloud")
NEXTCLOUD = GroupwareSystemName("nextcloud")
STALWART = GroupwareSystemName("stalwart")


class AppState(TypedDict):
    """Type hints for the Starlette state dict."""

    db: AsyncEngine


Request = starlette.requests.Request[AppState]


class EventSource(BaseModel):
    """Records provenance information for a calendar event."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    system: GroupwareSystemName
    original_id: UUID4 | str = Field(
        description="""\
            some opaque ID that the original system can use to uniquely identify
            the event. In CalDAV, this is the UID property of the VEVENT object.

            the target system should keep a mapping from this ID to its own
            ID, so that future deletions and updates can refer to the same event
            in the target system.
        """
    )


class Interval(BaseModel, ABC):  # pyright: ignore[reportUnsafeMultipleInheritance]
    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
    )

    @abstractmethod
    def start_datetime(self) -> datetime:
        """The canonical start datetime."""

    @abstractmethod
    def end_datetime(self) -> datetime:
        """The canonical end datetime."""


class DatetimeInterval(Interval):
    start: datetime
    """The inclusive datetime the event started on."""

    end: datetime
    """The exclusive datetime the event ends on."""

    @override
    def start_datetime(self) -> datetime:
        return self.start

    @override
    def end_datetime(self) -> datetime:
        return self.end

    @model_validator(mode="after")
    def _check_datetime_timezone(self) -> Self:
        if self.start.tzinfo is None:
            raise ValueError("start time missing a timezone")
        if self.end.tzinfo is None:
            raise ValueError("end time missing a timezone")
        return self


class FullDayInterval(Interval):
    """
    Models a full-day interval. This has the same semantics as iCal, in that
    it explicitly does not take timezones into account. A full day event should
    appear on the same date in any timezone.
    """

    start: date
    """The inclusive date the event started on."""

    end: date
    """The exclusive date the event ends on."""

    @override
    def start_datetime(self) -> datetime:
        return datetime.combine(date=self.start, time=time(), tzinfo=None)

    @override
    def end_datetime(self) -> datetime:
        return datetime.combine(date=self.end, time=time(), tzinfo=None)


class FreeBusyStatus(StrEnum):
    UNKNOWN = "unknown"
    FREE = "free"
    BUSY = "busy"
    TENTATIVE = "tentative"


class FreeBusy(BaseModel):
    """Sent to signal that the free/busy status in a specific interval should be
    changed on the target calendar."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    source: EventSource
    timestamp: datetime
    interval: Interval
    free_busy_status: FreeBusyStatus

    @model_validator(mode="after")
    def _check_datetime_timezone(self) -> Self:
        if self.timestamp.tzinfo is None:
            raise ValueError("event timestamps missing a timezone")
        return self


class CalendarEvent(BaseModel):
    """A full calendar event."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    source: EventSource | None
    """The source points to the system that owns the original copy. If it isn't
    set, this calendar event is not a copy."""

    interval: Interval
    subject: str | None = None
    free_busy_status: FreeBusyStatus


class EventPatch(BaseModel):
    """An update to an event, represents only the fields that should be updated.

    The special `MISSING` sentinel is used to mark a field that should be left
    unchanged."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    source: EventSource
    """The source is used to locate the event on the target system that should
    be updated."""

    interval: Interval | MISSING = MISSING
    subject: str | None | MISSING = MISSING
    free_busy_status: FreeBusyStatus | MISSING = MISSING

    def is_noop(self) -> bool:
        """Whether this patch modifies any fields. If no fields are modified,
        by the patch, this returns true."""

        # check that all fields that can be MISSING are MISSING
        return (
            self.interval is MISSING
            and self.subject is MISSING
            and self.free_busy_status is MISSING
        )


class User(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    name: str = Field(
        description="""\
            A human-readable name for the user. This cannot be relied upon for
            unique identification.
        """
    )
    id: str = Field(
        description="""\
            A stable unique opaque persistent identifier that only the target
            system understands.
        """
    )
    mail: str | None = Field(
        default=None,
        description="""\
            The primary mail address, if the directory has one. Other systems
            find the user by this address.
        """,
    )

    @override
    def __hash__(self) -> int:
        return hash(self.id)

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, User):
            return NotImplemented
        return self.id == other.id


_SYSTEM_PAIR_RE = re.compile(
    r"msync-(?P<source>[a-zA-Z0-9_]+)-(?P<mirror>[a-zA-Z0-9_]+)"
)


class Group(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    name: str
    id: str | None = None

    # XXX: workaround for https://github.com/microsoft/pyright/issues/6481
    if TYPE_CHECKING:

        @override
        def __hash__(self) -> int: ...

    def system_names(self) -> tuple[GroupwareSystemName, GroupwareSystemName] | None:
        if (matches := _SYSTEM_PAIR_RE.fullmatch(self.name)) is None:
            return None
        source = matches.group("source")
        mirror = matches.group("mirror")
        return (
            GroupwareSystemName(source),
            GroupwareSystemName(mirror),
        )


type Json = dict[str, Json] | list[Json] | str | int | float | bool | None
"""An arbitrary JSON value."""
