# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from datetime import datetime, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, NewType, Self, TypedDict

import starlette.requests
from pydantic import UUID4, BaseModel, ConfigDict, Field
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


class UpdateSource(BaseModel):
    """Records provenance information for an update."""

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


class Interval(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    full_day: bool = False
    start: datetime = Field(
        description="""\
            The inclusive datetime the event started on.

            If `full_day` is true, the time part is ignored.
        """
    )
    end: datetime = Field(
        description="""\
            The exclusive datetime the event ends on.

            If `full_day` is true, the time part is ignored. Note that it is
            still exclusive.
        """
    )

    @model_validator(mode="after")
    def _check_datetime_utc(self) -> Self:
        if self.start.tzinfo != timezone.utc:
            raise ValueError("start time must be UTC")
        if self.end.tzinfo != timezone.utc:
            raise ValueError("end time must be UTC")
        return self


class FreeBusyStatus(StrEnum):
    UNKNOWN = "unknown"
    FREE = "free"
    BUSY = "busy"
    TENTATIVE = "tentative"


class UpdateBase(BaseModel):
    """Base class with fields common to all kinds of updates."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    source: UpdateSource
    timestamp: datetime

    @model_validator(mode="after")
    def _check_datetime_utc(self) -> Self:
        if self.timestamp.tzinfo != timezone.utc:
            raise ValueError("event timestamps must be UTC")
        return self


class EventCreate(UpdateBase):
    """An event was created on the source calendar."""

    interval: Interval
    free_busy_status: FreeBusyStatus = FreeBusyStatus.BUSY


class EventDelete(UpdateBase):
    """An event was deleted from the source calendar."""


class EventEdit(UpdateBase):
    """An event's properties were modified by the source calendar. If a field
    is `None` it is unchanged."""

    interval: Interval | None = None
    free_busy_status: FreeBusyStatus | None = None


class FreeBusy(UpdateBase):
    """Sent to signal that the free/busy status in a specific interval should be
    changed on the target calendar."""

    interval: Interval
    free_busy_status: FreeBusyStatus


class CalendarEvent(BaseModel):
    """An event as read from a calendar: when it is and whether it takes time,
    and nothing else. One occurrence of a series is an event of its own."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    original_id: str = Field(
        description="""\
            The ID the source system knows the event by, used as
            `UpdateSource.original_id`. In CalDAV, this is the UID, or
            `<UID>/<RECURRENCE-ID>` for one occurrence of a series.
        """
    )
    interval: Interval
    free_busy_status: FreeBusyStatus


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

    @override
    def __hash__(self) -> int:
        return hash(self.id)

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, User):
            return NotImplemented
        return self.id == other.id


class Group(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    name: str
    id: str | None = None

    # XXX: workaround for https://github.com/microsoft/pyright/issues/6481
    if TYPE_CHECKING:

        @override
        def __hash__(self) -> int: ...
