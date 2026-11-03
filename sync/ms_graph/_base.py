# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0


from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import override

from ..model import Group, User


@dataclass(frozen=True)
class DeltaLink:
    url: str

    @override
    def __str__(self) -> str:
        return self.url


class MsGraph(ABC):
    @abstractmethod
    def list_groups(self, prefix: str) -> AsyncIterator[Group]: ...

    @abstractmethod
    def list_group_members(self, group_id: str) -> AsyncIterator[User]: ...
