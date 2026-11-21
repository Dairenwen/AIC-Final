from __future__ import annotations

import math
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    JsonValue as PydanticJsonValue,
)


JsonScalar = str | int | float | bool | None


def _validate_json_value(value: object) -> object:
    value_type = type(value)

    if value is None or value_type in (str, int, bool):
        return value

    if value_type is float:
        if not math.isfinite(value):
            raise ValueError("JSON 浮点数必须是有限值")
        return value

    if value_type is list:
        for item in value:
            _validate_json_value(item)
        return value

    if value_type is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("JSON 对象的键必须是字符串")
            _validate_json_value(item)
        return value

    raise ValueError(f"值不是严格 JSON 类型: {value_type.__name__}")


def _validate_json_object(value: object) -> object:
    if type(value) is not dict:
        raise ValueError("值必须是 JSON 对象")
    return _validate_json_value(value)


JsonValue = Annotated[PydanticJsonValue, BeforeValidator(_validate_json_value)]
JsonObject = Annotated[dict[str, JsonValue], BeforeValidator(_validate_json_object)]


class ApiSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
    )
