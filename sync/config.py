# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0

from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DirectoryServiceName(StrEnum):
    DUMMY = "dummy"
    MICROSOFT_CLOUD = "microsoft_cloud"


class MicrosoftCloudSettings(BaseModel):
    tenant_id: str
    client_id: str
    client_secret: SecretStr


class Settings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MSYNC__",
        env_nested_delimiter="__",
    )

    directory_service: DirectoryServiceName

    microsoft_cloud: MicrosoftCloudSettings | None


def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
