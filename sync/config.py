# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from datetime import timedelta
from enum import StrEnum
from typing import ClassVar, Self

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DirectoryServiceName(StrEnum):
    DUMMY = "dummy"
    MICROSOFT_CLOUD = "microsoft_cloud"


class MicrosoftCloudSettings(BaseModel):
    tenant_id: str
    client_id: str
    client_secret: SecretStr
    full_sync_period: timedelta = timedelta(hours=25)


class NextcloudSettings(BaseModel):
    url: str
    service_user: str = Field(
        description=(
            "A normal account that ghost calendars are shared with. It must be"
            " allowed to share with every account, so free/busy finds every"
            " address in use."
        )
    )
    service_password: SecretStr
    manager_user: str = Field(
        description="A sub-admin of the ghost group that creates and edits ghosts."
    )
    manager_password: SecretStr
    ghost_group: str = Field(
        default="msync-ghosts",
        description="The group of ghost accounts, which the manager is sub-admin of.",
    )
    calendar_timezone: str = Field(
        default="Europe/Copenhagen",
        description="The timezone Nextcloud places full days of ghost calendars in.",
    )
    allow_http: bool = Field(
        default=False, description="Only for local tests: passwords travel in clear."
    )

    @model_validator(mode="after")
    def _check_url_uses_https(self) -> Self:
        if not self.allow_http and not self.url.lower().startswith("https:"):
            raise ValueError("the Nextcloud URL must use https")
        return self


class Settings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MSYNC__",
        env_nested_delimiter="__",
    )

    directory_service: DirectoryServiceName

    microsoft_cloud: MicrosoftCloudSettings | None

    nextcloud: NextcloudSettings | None = None

    database_url: str = "postgresql+psycopg://msync:msync@postgres:5432/msync"


def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
