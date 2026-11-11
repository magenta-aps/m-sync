# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import pytest

from sync.groupware.nextcloud.calendars import (
    get_ghost_calendar_href,
    read_ghost_calendar_href,
)


@pytest.mark.parametrize("owner", ["anne", "anne bo", "anne_shared_by_bo"])
def test_the_href_of_a_ghost_calendar_gives_its_owner(owner: str) -> None:
    href = get_ghost_calendar_href("msync", owner)

    assert read_ghost_calendar_href(href) == (owner, href)
    # A Nextcloud in a webroot gives the same href.
    assert read_ghost_calendar_href(f"/nextcloud{href}") == (owner, href)


@pytest.mark.parametrize(
    "href",
    [
        "/remote.php/dav/calendars/msync/personal/",
        "/remote.php/dav/calendars/msync/personal_shared_by_anne/",
        "/remote.php/dav/calendars/msync/xmsync_shared_by_anne/",
        "/remote.php/dav/calendars/msync/msync_shared_by_/",
        "/remote.php/dav/calendars/msync/msync_shared_by_anne/planning.ics",
    ],
)
def test_any_other_href_is_not_a_ghost_calendar(href: str) -> None:
    assert read_ghost_calendar_href(href) is None
