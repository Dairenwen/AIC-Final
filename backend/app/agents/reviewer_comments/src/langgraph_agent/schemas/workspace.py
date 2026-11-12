from __future__ import annotations

from enum import Enum
from typing import Annotated, Self

from pydantic import (
    AwareDatetime,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .common import ApiSchema


NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class WorkspaceMode(str, Enum):
    FAST = "FAST"
    SLOW = "SLOW"


class SettingsSource(str, Enum):
    SYSTEM_DEFAULT = "system_default"
    USER_LONG_TERM = "user_long_term"
    WORKSPACE_OVERRIDE = "workspace_override"
    SOURCE_OVERRIDE = "source_override"


class SettingsScope(str, Enum):
    CURRENT_TASK = "current_task"
    SAVE_AS_DEFAULT = "save_as_default"


class ResponseSettings(ApiSchema):
    response_language: NonBlankStr
    tone: NonBlankStr
    author_reference: NonBlankStr
    target_length: NonBlankStr
    terminology_preferences: list[str] = Field(default_factory=list)
    source: SettingsSource
    copied_from_store_at: AwareDatetime | None = None

    @field_validator("terminology_preferences")
    @classmethod
    def normalize_terminology_preferences(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()

        for value in values:
            stripped = value.strip()
            if not stripped:
                raise ValueError("术语不能为空")
            if stripped not in seen:
                normalized.append(stripped)
                seen.add(stripped)

        return normalized


class ResponseSettingsInput(ApiSchema):
    """客户端可写的表达设置字段，不接受服务端来源元数据。"""

    response_language: NonBlankStr
    tone: NonBlankStr
    author_reference: NonBlankStr
    target_length: NonBlankStr
    terminology_preferences: list[str] = Field(default_factory=list)

    @field_validator("terminology_preferences")
    @classmethod
    def normalize_terminology_preferences(cls, values: list[str]) -> list[str]:
        return ResponseSettings.normalize_terminology_preferences(values)

    def with_source(self, source: SettingsSource) -> ResponseSettings:
        return ResponseSettings(
            **self.model_dump(mode="python"),
            source=source,
        )


class UpdateSourceSettingsCommand(ApiSchema):
    inherit_workspace: bool
    settings: ResponseSettingsInput | None = None

    @model_validator(mode="after")
    def validate_settings_mode(self) -> Self:
        if self.inherit_workspace and self.settings is not None:
            raise ValueError("继承工作区设置时不能同时提供 settings")
        if not self.inherit_workspace and self.settings is None:
            raise ValueError("自定义来源设置时必须提供 settings")
        return self


class UpdateSettingsCommand(ApiSchema):
    settings: ResponseSettings
    scope: SettingsScope
