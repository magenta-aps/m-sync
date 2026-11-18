# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

"""Stretches of time as instants in UTC, and busy time as Nextcloud shows it."""

from dataclasses import dataclass
from datetime import datetime

from ..model import FreeBusyStatus


@dataclass(frozen=True, order=True)
class Span:
    """UTC instants that sort by start, then end. An Interval has full_day
    instead, and no order."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class BusyPeriod:
    """Busy time as Nextcloud shows it: a period in a free/busy answer."""

    start: datetime
    end: datetime
    status: FreeBusyStatus = FreeBusyStatus.BUSY
