# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

"""Nextcloud's OCS API and the ghost accounts the manager keeps over it."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Annotated, ClassVar, Self, TypedDict, final, override

import httpx
import structlog
from pydantic import BaseModel, BeforeValidator, ConfigDict, SecretStr, ValidationError

from .connection import (
    NextcloudError,
    NotEditableError,
    NotFoundError,
    RateLimitedError,
    UserExistsError,
    make_http_client,
    quote_path_segment,
    send_request,
)

logger = structlog.stdlib.get_logger()

# Nextcloud accepts at most 500 per page. A page answers well within the
# connection's 30 s timeout.
_GHOSTS_PER_PAGE = 500

_USERS_PATH = "/ocs/v2.php/cloud/users"


class OcsMeta(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    status: str
    statuscode: int | None = None
    message: str | None = None


class OcsAnswer[DataType](BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    meta: OcsMeta
    data: DataType


class _OcsBody[DataType](BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    ocs: OcsAnswer[DataType]


class OcsClient:
    _http_client: httpx.AsyncClient

    def __init__(
        self, url: str, auth: httpx.Auth, *, keep_cookies: bool = True
    ) -> None:
        self._http_client = make_http_client(url, auth, keep_cookies)

    async def call_ocs[DataType](
        self,
        method: str,
        path: str,
        data_type: type[DataType],
        *,
        params: Mapping[str, str | int] | None = None,
        form: Mapping[str, str] | None = None,
    ) -> OcsAnswer[DataType]:
        """The answer with its data read as `data_type`. Raises NextcloudError if
        the call failed or the answer is not what we expect."""
        response = await send_request(
            self._http_client,
            method,
            path,
            headers={"Accept": "application/json"},
            params=params,
            data=form,
        )
        if response.status_code == 429:
            raise RateLimitedError(f"{method} {path}: rate limited")
        try:
            # First as object: a failure's data does not fit `data_type`.
            meta = _OcsBody[object].model_validate_json(response.content).ocs.meta
            # v1 answers 100 and v2 answers 200, so the status word decides.
            if meta.status == "ok":
                return _OcsBody[data_type].model_validate_json(response.content).ocs
        except ValidationError:
            # Our own message, since pydantic's quotes the answer with names and
            # addresses.
            raise NextcloudError(
                f"{method} {path}: unexpected answer, HTTP {response.status_code}"
            ) from None
        raise _make_failure_error(method, path, response.status_code, meta)

    async def close(self) -> None:
        await self._http_client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exception_info: object) -> None:
        await self.close()


def _make_failure_error(
    method: str, path: str, http_status: int, meta: OcsMeta
) -> NextcloudError:
    failure = f"{method} {path}: {meta.message or f'OCS {meta.statuscode}'}"
    # 998 is what a sub-admin gets for a user outside its groups.
    if http_status == 404 or meta.statuscode in (404, 998):
        return NotFoundError(failure)
    # 113 is a field the caller may not edit.
    if meta.statuscode == 113:
        return NotEditableError(failure)
    # 102 from a create means the user exists. Other calls answer 102 for other
    # failures.
    if method == "POST" and path == _USERS_PATH and meta.statuscode == 102:
        return UserExistsError(failure)
    return NextcloudError(failure)


class NextcloudAccount(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: str
    # Required: a user whose details we may not see comes as its id alone.
    email: str | None
    enabled: bool


def _empty_list_as_map(value: object) -> object:
    # PHP sends an empty map as an empty JSON list.
    return {} if value == [] else value


# OCS wraps lists in an object, so these are the shapes of its data.
class _GhostPage(TypedDict):
    users: Annotated[dict[str, object], BeforeValidator(_empty_list_as_map)]


class _UserIds(TypedDict):
    users: list[str]


class _GroupIds(TypedDict):
    groups: list[str]


class NextcloudAccounts(ABC):
    @abstractmethod
    async def list_ghosts(self, group: str) -> dict[str, NextcloudAccount]: ...

    @abstractmethod
    async def get_account(self, user_id: str) -> NextcloudAccount: ...

    @abstractmethod
    async def list_members(self, group: str) -> set[str]: ...

    @abstractmethod
    async def is_in_group(self, user_id: str, group: str) -> bool: ...

    @abstractmethod
    async def create_user(
        self, user_id: str, display_name: str, password: SecretStr, group: str
    ) -> None: ...

    @abstractmethod
    async def edit_user(
        self, user_id: str, field_name: str, field_value: str
    ) -> None: ...

    @abstractmethod
    async def set_password(self, user_id: str, password: SecretStr) -> None: ...

    @abstractmethod
    async def set_enabled(self, user_id: str, *, enabled: bool) -> None: ...

    @abstractmethod
    async def set_own_email(
        self, user_id: str, password: SecretStr, email: str
    ) -> None: ...

    @abstractmethod
    async def delete_user(self, user_id: str) -> None: ...


@final
class RealNextcloudAccounts(NextcloudAccounts):
    _ocs_client: OcsClient
    _url: str
    _page_size: int

    def __init__(
        self, ocs_client: OcsClient, url: str, page_size: int = _GHOSTS_PER_PAGE
    ) -> None:
        self._ocs_client = ocs_client
        self._url = url
        self._page_size = page_size

    @override
    async def list_ghosts(self, group: str) -> dict[str, NextcloudAccount]:
        """Every readable member with its details, a page at a time. Unreadable
        members are left out. Raises NextcloudError if no member is readable.
        Paging by offset is no snapshot, so a member added or removed meanwhile
        can shift another one past us."""
        path = f"/ocs/v2.php/cloud/groups/{quote_path_segment(group)}/users/details"
        ghosts: dict[str, NextcloudAccount] = {}
        seen_ids: set[str] = set()
        offset = 0
        while True:
            params = {"limit": self._page_size, "offset": offset}
            answer = await self._ocs_client.call_ocs(
                "GET", path, _GhostPage, params=params
            )
            page = answer.data["users"]
            # A page without new ids means Nextcloud ignored the offset.
            if page and page.keys() <= seen_ids:
                raise NextcloudError(f"GET {path}: offset ignored")
            seen_ids |= page.keys()
            ghosts |= _read_ghost_accounts(page)
            if len(page) < self._page_size:
                break
            offset += self._page_size
        # Members, but none readable: the format has changed.
        if seen_ids and not ghosts:
            raise NextcloudError(f"GET {path}: no readable ghosts")
        return ghosts

    @override
    async def get_account(self, user_id: str) -> NextcloudAccount:
        """One user's details. A sub-admin gets NotFoundError for users outside
        its groups."""
        path = f"/ocs/v2.php/cloud/users/{quote_path_segment(user_id)}"
        answer = await self._ocs_client.call_ocs("GET", path, NextcloudAccount)
        return answer.data

    @override
    async def list_members(self, group: str) -> set[str]:
        """Only the user ids, which is cheap even for a large group."""
        path = f"/ocs/v2.php/cloud/groups/{quote_path_segment(group)}/users"
        answer = await self._ocs_client.call_ocs("GET", path, _UserIds)
        return set(answer.data["users"])

    @override
    async def is_in_group(self, user_id: str, group: str) -> bool:
        path = f"/ocs/v2.php/cloud/users/{quote_path_segment(user_id)}/groups"
        try:
            answer = await self._ocs_client.call_ocs("GET", path, _GroupIds)
        except NotFoundError:
            # A sub-admin gets "not found" for anyone outside its groups.
            return False
        return group in answer.data["groups"]

    @override
    async def create_user(
        self, user_id: str, display_name: str, password: SecretStr, group: str
    ) -> None:
        """Create a user in `group` without an e-mail address, so Nextcloud sends
        no welcome mail. A create that fails half-way can leave the user outside
        `group`. Only a Nextcloud admin can see and delete it there. Raises
        UserExistsError if the user exists already."""
        # Form-encoded, so a value like `Bo&groups[]=admin` cannot add
        # parameters we never sent.
        form = {
            "userid": user_id,
            "password": password.get_secret_value(),
            "displayName": display_name,
            "groups[]": group,
        }
        _ = await self._ocs_client.call_ocs("POST", _USERS_PATH, object, form=form)

    @override
    async def edit_user(self, user_id: str, field_name: str, field_value: str) -> None:
        """Set one field, e.g. `email`. Limited to 50 per 10 minutes."""
        path = f"/ocs/v2.php/cloud/users/{quote_path_segment(user_id)}"
        form = {"key": field_name, "value": field_value}
        _ = await self._ocs_client.call_ocs("PUT", path, object, form=form)

    @override
    async def set_password(self, user_id: str, password: SecretStr) -> None:
        path = f"/ocs/v2.php/cloud/users/{quote_path_segment(user_id)}"
        form = {"key": "password", "value": password.get_secret_value()}
        _ = await self._ocs_client.call_ocs("PUT", path, object, form=form)

    @override
    async def set_enabled(self, user_id: str, *, enabled: bool) -> None:
        """A disabled user cannot log in."""
        action = "enable" if enabled else "disable"
        path = f"/ocs/v2.php/cloud/users/{quote_path_segment(user_id)}/{action}"
        _ = await self._ocs_client.call_ocs("PUT", path, object)

    @override
    async def set_own_email(
        self, user_id: str, password: SecretStr, email: str
    ) -> None:
        """As the user itself, so the edit counts against the user's own rate
        limit. Raises NotEditableError if Nextcloud lets no user change its own
        address."""
        path = f"/ocs/v2.php/cloud/users/{quote_path_segment(user_id)}"
        form = {"key": "email", "value": email}
        user_auth = httpx.BasicAuth(user_id, password.get_secret_value())
        # A client of its own without cookies: the user's session must not leak.
        async with OcsClient(
            self._url, user_auth, keep_cookies=False
        ) as user_ocs_client:
            _ = await user_ocs_client.call_ocs("PUT", path, object, form=form)

    @override
    async def delete_user(self, user_id: str) -> None:
        """Delete a user with its calendars."""
        path = f"/ocs/v2.php/cloud/users/{quote_path_segment(user_id)}"
        _ = await self._ocs_client.call_ocs("DELETE", path, object)


def _read_ghost_accounts(page: Mapping[str, object]) -> dict[str, NextcloudAccount]:
    """The readable ghosts on a page. Each unreadable one is logged and skipped."""
    ghosts: dict[str, NextcloudAccount] = {}
    for user_id, details in page.items():
        try:
            ghosts[user_id] = NextcloudAccount.model_validate(details)
        except ValidationError:
            # Our own message, since pydantic's quotes names and addresses.
            logger.warning("unreadable ghost", user_id=user_id)
    return ghosts
