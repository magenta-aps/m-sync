# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import pytest

from sync.config import Settings
from sync.connectors.microsoft_cloud import MicrosoftCloudConnector


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
    users =
