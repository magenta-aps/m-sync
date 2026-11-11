# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

import httpx
import pytest

from sync.groupware.nextcloud.connection import NextcloudError
from sync.groupware.nextcloud.ocs import OcsClient, RealNextcloudAccounts


async def test_a_network_error_does_not_show_the_password() -> None:
    # Nothing listens on port 9, the discard port.
    unreachable_auth = httpx.BasicAuth("manager", "very-secret-password")
    async with OcsClient("http://127.0.0.1:9", unreachable_auth) as ocs_client:
        accounts = RealNextcloudAccounts(ocs_client, "http://127.0.0.1:9")

        with pytest.raises(NextcloudError) as raised:
            _ = await accounts.get_account("anne")

    assert "very-secret-password" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
