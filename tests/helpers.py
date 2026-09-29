# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from collections.abc import AsyncIterator

from sync.model import Group, User


class DummyDirectoryService:
    def list_groups(self) -> AsyncIterator[Group]:
        raise NotImplementedError

    def list_group_members(
        self,
        group: Group,
    ) -> AsyncIterator[User]:
        _ = group
        raise NotImplementedError
