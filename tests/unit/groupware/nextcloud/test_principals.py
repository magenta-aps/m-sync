# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

"""Free/busy answers read without a network, including those Nextcloud cannot give."""

from datetime import UTC, datetime
from xml.etree import ElementTree

import pytest
from icalendar import Calendar, FreeBusy

from sync.groupware.nextcloud.principals import read_schedule_response
from sync.model import FreeBusyStatus
from sync.nextcloud.spans import BusyPeriod

MONDAY_NINE = datetime(2030, 1, 7, 9, tzinfo=UTC)
MONDAY_TEN = datetime(2030, 1, 7, 10, tzinfo=UTC)


@pytest.mark.parametrize(
    "schedule_response",
    [
        (
            '<cal:schedule-response xmlns:cal="urn:ietf:params:xml:ns:caldav">'
            "<cal:response><cal:request-status>5.3;No scheduling support"
            "</cal:request-status></cal:response></cal:schedule-response>"
        ),
        (
            '<cal:schedule-response xmlns:cal="urn:ietf:params:xml:ns:caldav">'
            "<cal:response><cal:request-status>3.8;No authority"
            "</cal:request-status></cal:response></cal:schedule-response>"
        ),
        '<cal:schedule-response xmlns:cal="urn:ietf:params:xml:ns:caldav"/>',
    ],
    ids=["no scheduling support", "no authority", "no answer"],
)
def test_free_busy_without_an_answer_is_unknown_not_free(
    schedule_response: str,
) -> None:
    assert read_schedule_response(ElementTree.fromstring(schedule_response)) is None


def test_free_busy_with_an_answer_is_read() -> None:
    free_busy = FreeBusy()
    free_busy.add("freebusy", (MONDAY_NINE, MONDAY_TEN))  # pyright: ignore[reportUnknownMemberType]
    free_busy_answer = Calendar()
    free_busy_answer.add_component(free_busy)
    schedule_response = (
        '<cal:schedule-response xmlns:cal="urn:ietf:params:xml:ns:caldav">'
        "<cal:response><cal:request-status>2.0;Success</cal:request-status>"
        f"<cal:calendar-data>{free_busy_answer.to_ical().decode()}"
        "</cal:calendar-data></cal:response></cal:schedule-response>"
    )

    assert read_schedule_response(ElementTree.fromstring(schedule_response)) == [
        BusyPeriod(start=MONDAY_NINE, end=MONDAY_TEN, status=FreeBusyStatus.BUSY)
    ]
