from __future__ import annotations

from enum import Enum
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from .common import ApiSchema, JsonObject, JsonValue


NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class EditableFieldType(str, Enum):
    TEXT = "text"
    TEXTAREA = "textarea"
    SELECT = "select"
    MULTISELECT = "multiselect"
    CHECKBOX = "checkbox"


class InteractionOption(ApiSchema):
    value: JsonValue
    label: str
    description: str | None = None
    disabled: bool = False


class EditableField(ApiSchema):
    key: NonBlankStr
    label: str
    type: EditableFieldType
    required: bool = False
    default: JsonValue = None
    choices: list[InteractionOption] = Field(default_factory=list)
    help_text: str | None = None

    @model_validator(mode="after")
    def validate_field_contract(self) -> Self:
        if self.type in (EditableFieldType.SELECT, EditableFieldType.MULTISELECT):
            if not self.choices:
                raise ValueError("select 和 multiselect 字段必须提供非空 choices")

        if (
            self.type is EditableFieldType.CHECKBOX
            and self.default is not None
            and type(self.default) is not bool
        ):
            raise ValueError("checkbox 字段的 default 只能是布尔值")

        return self


class PendingInteraction(ApiSchema):
    interaction_id: UUID
    interaction_type: NonBlankStr
    workspace_id: UUID
    suggestion_id: UUID | None
    source_id: UUID | None
    thread_id: NonBlankStr
    input_version: NonBlankStr
    title: str
    question: str
    context: JsonObject
    options: list[InteractionOption]
    editable_fields: list[EditableField]
    blockers: list[str]
    resume_action: NonBlankStr


class ResumeCommand(ApiSchema):
    workspace_id: UUID
    thread_id: NonBlankStr
    interaction_id: UUID
    input_version: NonBlankStr
    payload: JsonObject
