# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

"""Everything msync does in Nextcloud, by the account that does it. Every
failure, including network errors, is raised as NextcloudError."""

from abc import ABC, abstractmethod
from typing import Self, final, override

import httpx

from ...config import NextcloudSettings
from .calendars import NextcloudCalendars, RealNextcloudCalendars
from .dav import DavClient
from .ocs import NextcloudAccounts, OcsClient, RealNextcloudAccounts
from .principals import NextcloudPrincipals, RealNextcloudPrincipals


class NextcloudClient(ABC):
    accounts: NextcloudAccounts
    calendars: NextcloudCalendars
    principals: NextcloudPrincipals

    @abstractmethod
    async def close(self) -> None: ...

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exception_info: object) -> None:
        await self.close()


@final
class RealNextcloudClient(NextcloudClient):
    _ocs_client: OcsClient
    _service_dav_client: DavClient

    def __init__(self, settings: NextcloudSettings) -> None:
        """The manager creates and edits ghosts over OCS. The service account
        reads and writes calendars over CalDAV. Each has its own connection, so
        cookies never carry over. Both keep their session, since a password
        login on every request is several times slower. Each such login also
        leaves a temporary login token that expires after a day unused."""
        manager_auth = httpx.BasicAuth(
            settings.manager_user, settings.manager_password.get_secret_value()
        )
        service_auth = httpx.BasicAuth(
            settings.service_user, settings.service_password.get_secret_value()
        )
        self._ocs_client = OcsClient(settings.url, manager_auth)
        self._service_dav_client = DavClient(settings.url, service_auth)
        self.accounts = RealNextcloudAccounts(self._ocs_client, settings.url)
        self.calendars = RealNextcloudCalendars(
            self._service_dav_client, settings.url, settings.service_user
        )
        self.principals = RealNextcloudPrincipals(
            self._service_dav_client, settings.service_user
        )

    @override
    async def close(self) -> None:
        try:
            await self._ocs_client.close()
        finally:
            await self._service_dav_client.close()
