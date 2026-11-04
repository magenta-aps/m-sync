# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from datetime import UTC, datetime

from icalendar import Calendar

from sync.model import MICROSOFT_CLOUD, NEXTCLOUD, FreeBusyStatus, UpdateSource
from sync.nextcloud.busy_blocks import (
    make_busy_block_name,
    read_busy_block,
    read_busy_block_timestamp,
    render_busy_block,
)
from sync.nextcloud.spans import BusyPeriod
from tests.unit.nextcloud.helpers import NINE, TEN, make_calendar, make_event

PLANNING_MEETING = UpdateSource(system=MICROSOFT_CLOUD, original_id="planning")


def test_a_written_busy_block_reads_back() -> None:
    written_at = datetime(2026, 9, 30, 12, tzinfo=UTC)
    ics = render_busy_block(
        "msync-busy-planning.ics", NINE, TEN, FreeBusyStatus.TENTATIVE, written_at
    )

    calendar = Calendar.from_ical(ics)

    assert read_busy_block(calendar) == BusyPeriod(
        start=NINE, end=TEN, status=FreeBusyStatus.TENTATIVE
    )
    assert read_busy_block_timestamp(calendar) == written_at


def test_the_same_update_gives_the_same_bytes() -> None:
    def render() -> bytes:
        return render_busy_block(
            "msync-busy-planning.ics", NINE, TEN, FreeBusyStatus.BUSY, NINE
        )

    assert render() == render()


def test_an_event_with_a_date_is_not_a_busy_block() -> None:
    all_day = make_event(start=NINE.date(), end=TEN.date())

    assert read_busy_block(make_calendar(all_day)) is None


def test_the_name_is_safe_and_the_same_for_the_same_source() -> None:
    occurrence = UpdateSource(system=NEXTCLOUD, original_id="a/b+c=" * 100)

    name = make_busy_block_name(occurrence)

    assert name == make_busy_block_name(occurrence)
    assert name != make_busy_block_name(PLANNING_MEETING)
    assert name.startswith("msync-busy-") and name.endswith(".ics")
    assert len(name) == len("msync-busy-") + 32 + len(".ics")
    assert "/" not in name
