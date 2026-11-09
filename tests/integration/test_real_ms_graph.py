# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import pytest
from more_itertools import one

from sync.config import Settings
from sync.ms_graph import MsGraph, RealMsGraph


@pytest.fixture
def ms_graph(
    settings: Settings,
) -> MsGraph:
    return RealMsGraph(settings)


async def test_auth(settings: Settings) -> None:
    """This just checks that a client is created succesfully."""

    _ = RealMsGraph(settings)


async def test_list_groups(ms_graph: MsGraph) -> None:
    groups = {group async for group in ms_graph.list_groups("msync-")}

    assert all(group.name.lower().startswith("msync-") for group in groups), groups
    assert any(
        group.name.lower() == "msync-microsoft_cloud-nextcloud" for group in groups
    )
    assert any(
        group.name.lower() == "msync-nextcloud-microsoft_cloud" for group in groups
    )


async def test_list_users(ms_graph: MsGraph) -> None:
    groups = {group async for group in ms_graph.list_groups("msync-")}

    group1 = one(
        group for group in groups if group.name == "msync-microsoft_cloud-nextcloud"
    )
    assert group1.id is not None
    user1 = one([user async for user in ms_graph.list_group_members(group1.id)])
    assert user1.name == "laurits-testensen@magentakommune.dk"
    assert user1.mail == "laurits-testensen@magentakommune.dk"

    group2 = one(
        group for group in groups if group.name == "msync-nextcloud-microsoft_cloud"
    )
    assert group2.id is not None
    user2 = one([user async for user in ms_graph.list_group_members(group2.id)])
    assert user2.name == "anne-testensen@magentakommune.dk"
    assert user2.mail == "anne-testensen@magentakommune.dk"
