# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import pytest
from more_itertools import one

from sync.config import Settings
from sync.directories.microsoft_cloud import GROUP_PREFIX, MicrosoftCloudDirectory
from sync.model import Group
from sync.ms_graph import make_graph_client


@pytest.fixture
def directory(
    settings: Settings,
) -> MicrosoftCloudDirectory:
    return MicrosoftCloudDirectory(make_graph_client(settings))


async def test_auth(settings: Settings) -> None:
    """This just checks that a client is created succesfully."""

    _ = make_graph_client(settings)


async def test_list_groups(directory: MicrosoftCloudDirectory) -> None:
    groups = {group async for group in directory.list_groups()}

    assert all(group.name.lower().startswith(GROUP_PREFIX) for group in groups), groups
    assert any(
        group.name.lower() == "msync-microsoft_cloud-nextcloud" for group in groups
    )
    assert any(
        group.name.lower() == "msync-nextcloud-microsoft_cloud" for group in groups
    )


async def test_list_users(directory: MicrosoftCloudDirectory) -> None:
    group1 = Group(name="msync-microsoft_cloud-nextcloud")
    user1 = one([user async for user in directory.list_group_members(group1)])
    assert user1.name == "laurits-testensen@magentakommune.dk"

    group2 = Group(name="msync-nextcloud-microsoft_cloud")
    user2 = one([user async for user in directory.list_group_members(group2)])
    assert user2.name == "anne-testensen@magentakommune.dk"
