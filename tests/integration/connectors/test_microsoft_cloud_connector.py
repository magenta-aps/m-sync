# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import pytest
from more_itertools import one

from sync.config import Settings
from sync.connectors.microsoft_cloud import MicrosoftCloudConnector
from sync.model import Group


@pytest.fixture
def connector(
    settings: Settings,
) -> MicrosoftCloudConnector:
    return MicrosoftCloudConnector(settings)


@pytest.mark.usefixtures("connector")
async def test_auth() -> None:
    """This test just uses the fixture, which triggers auth."""


async def test_list_groups(connector: MicrosoftCloudConnector) -> None:
    groups = {group async for group in connector.list_groups()}

    assert any(
        group.name.lower() == "msync-microsoft_cloud-nextcloud" for group in groups
    )
    assert any(
        group.name.lower() == "msync-nextcloud-microsoft_cloud" for group in groups
    )


async def test_list_users(connector: MicrosoftCloudConnector) -> None:
    group1 = Group(name="msync-microsoft_cloud-nextcloud")
    user1 = one([user async for user in connector.list_group_members(group1)])
    assert user1.name == "laurits-testensen@magentakommune.dk"

    group2 = Group(name="msync-nextcloud-microsoft_cloud")
    user2 = one([user async for user in connector.list_group_members(group2)])
    assert user2.name == "anne-testensen@magentakommune.dk"
