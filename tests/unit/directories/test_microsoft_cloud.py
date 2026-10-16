# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


import pytest
from more_itertools import one

from sync.directories.microsoft_cloud import (
    GroupNotFoundError,
    MicrosoftCloudDirectory,
)
from sync.model import Group, User
from tests.helpers import DummyMsGraph


@pytest.fixture
def dummy_ms_graph() -> DummyMsGraph:
    return DummyMsGraph()


@pytest.fixture
def directory(dummy_ms_graph: DummyMsGraph) -> MicrosoftCloudDirectory:
    return MicrosoftCloudDirectory(dummy_ms_graph)


async def test_list_group_members_resolves_name_to_id(
    dummy_ms_graph: DummyMsGraph,
    directory: MicrosoftCloudDirectory,
) -> None:
    u1 = User(name="Alice", id="u1")
    u2 = User(name="Bob", id="u2")

    dummy_ms_graph.group_ids = {"msync-foo": "g1"}
    dummy_ms_graph.group_members = {"g1": [u1, u2]}

    # we search using the group name, but internally, the group name has to
    # be mapped to the group id, in this case "g1"
    group = Group(name="msync-foo", id=None)
    users = [user async for user in directory.list_group_members(group)]

    assert u1 in users
    assert u2 in users
    assert len(users) == 2


async def test_list_group_members_is_case_insensitive(
    dummy_ms_graph: DummyMsGraph,
    directory: MicrosoftCloudDirectory,
) -> None:
    u1 = User(name="Alice", id="u1")
    u2 = User(name="Bob", id="u2")

    dummy_ms_graph.group_ids = {
        "MsYnC-fOo": "g1",
        "msync-bar": "g2",
    }
    dummy_ms_graph.group_members = {
        "g1": [u1],
        "g2": [u2],
    }

    # lookup using a lowercase key, into g1, which has a mIxEd CaSe name
    group1 = Group(name="msync-foo", id=None)
    got_u1 = one([user async for user in directory.list_group_members(group1)])
    assert got_u1 == u1

    # lookup using a mIxEd CaSe key, into g2, which has a lowercase name
    group2 = Group(name="mSyNc-BaR", id=None)
    got_u2 = one([user async for user in directory.list_group_members(group2)])
    assert got_u2 == u2


async def test_list_group_members_unknown_group_raises(
    dummy_ms_graph: DummyMsGraph,
    directory: MicrosoftCloudDirectory,
) -> None:
    dummy_ms_graph.group_ids = {"msync-foo": "g1"}

    group = Group(name="msync-unknown", id=None)
    with pytest.raises(GroupNotFoundError):
        async for _ in directory.list_group_members(group):
            pass
