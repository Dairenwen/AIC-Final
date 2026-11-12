"""Model-independent structured output over Chat Completions."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock
import types
from typing import Annotated, Any, TypeVar, Union, get_args, get_origin
from urllib.parse import urlparse

import openai
from pydantic import BaseModel, ValidationError

from config.settings import get_settings

from .resilience import (
    LlmFinalError,
    LlmRetryableError,
    StructuredOutputParseError,
    is_retryable_llm_error,
)


def _llm_extra_body() -> dict[str, Any]:
    """读取 Chat Completions 可选 extra_body；兼容 Settings 实例方法或字段。"""
    settings = get_settings()
    method = getattr(settings, "llm_extra_body", None)
    if callable(method):
        value = method()
        return dict(value) if isinstance(value, dict) else {}
    value = getattr(settings, "LLM_EXTRA_BODY", {})
    return dict(value) if isinstance(value, dict) else {}


def _model_for(purpose: str) -> str:
    """按用途返回配置的模型名；兼容 Settings.model_for 或字段映射。"""
    settings = get_settings()
    method = getattr(settings, "model_for", None)
    if callable(method):
        return method(purpose)
    mapping = {
        "split": settings.MODEL_SPLIT,
        "analyze": settings.MODEL_ANALYZE,
        "draft": settings.MODEL_DRAFT,
        "paper_card": settings.MODEL_PAPER_CARD,
    }
    if purpose not in mapping:
        raise RuntimeError(
            f"未知的模型用途 '{purpose}'，仅支持：split、analyze、draft、paper_card。"
        )
    return mapping[purpose]


SchemaT = TypeVar("SchemaT", bound=BaseModel)
ClientFactory = Callable[..., Any]

_LOGGER = logging.getLogger(__name__)
_TRANSPORT_SCHEMA_VERSION = 1
# 方案 A：优先 json_schema 拿结构质量；渠道不支持时再降级，不把默认改成 json_object。
# 次要字段缺失由各业务 schema 的 default / Optional 消化，证据类字段仍保持必填。
_DEFAULT_STRATEGY_ORDER = (
    "native_json_schema",
    "forced_tool",
    "unforced_tool",
    "json_object",
    "prompt_json",
)
_REMOTE_ONLY_CONSTRAINTS = frozenset(
    {
        "$schema",
        "default",
        "deprecated",
        "examples",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "maxContains",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minContains",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "multipleOf",
        "pattern",
        "readOnly",
        "uniqueItems",
        "writeOnly",
    }
)


class StructuredOutputStrategy(StrEnum):
    """Provider-neutral Chat Completions request shapes."""

    NATIVE_JSON_SCHEMA = "native_json_schema"
    FORCED_TOOL = "forced_tool"
    UNFORCED_TOOL = "unforced_tool"
    JSON_OBJECT = "json_object"
    PROMPT_JSON = "prompt_json"


@dataclass(frozen=True)
class _CapabilityKey:
    base_url: str
    model_name: str
    config_fingerprint: str


@dataclass
class _CapabilityRecord:
    selected_strategy: StructuredOutputStrategy | None = None
    unsupported: dict[StructuredOutputStrategy, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _UnsupportedParameter:
    parameter: str
    reason: str


def _default_client_factory(**kwargs: Any) -> openai.OpenAI:
    return openai.OpenAI(**kwargs)


def _get_value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _normalize_messages(messages: Sequence[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    role_aliases = {
        "human": "user",
        "ai": "assistant",
    }
    for message in messages:
        if isinstance(message, tuple) and len(message) == 2:
            role, content = message
            normalized.append(
                {
                    "role": role_aliases.get(str(role), str(role)),
                    "content": content,
                }
            )
            continue
        if isinstance(message, Mapping):
            copied = dict(message)
            role = str(copied.get("role", "user"))
            copied["role"] = role_aliases.get(role, role)
            normalized.append(copied)
            continue

        role = str(getattr(message, "type", getattr(message, "role", "user")))
        normalized.append(
            {
                "role": role_aliases.get(role, role),
                "content": getattr(message, "content", str(message)),
            }
        )
    return normalized


def _clean_transport_schema(value: Any, *, container: str | None = None) -> Any:
    if isinstance(value, list):
        return [_clean_transport_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    cleaned: dict[str, Any] = {}
    names_are_data = container in {"$defs", "definitions", "properties"}
    for key, item in value.items():
        if not names_are_data and key in _REMOTE_ONLY_CONSTRAINTS:
            continue
        cleaned[key] = _clean_transport_schema(item, container=key)
    return cleaned


def _transport_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Return a portable schema copy; the original Pydantic model stays authoritative."""
    return _clean_transport_schema(schema.model_json_schema(mode="validation"))


def _tool_name(schema: type[BaseModel]) -> str:
    raw_name = re.sub(r"[^A-Za-z0-9_-]", "_", schema.__name__)
    if not raw_name:
        raw_name = "structured_output"
    if raw_name[0].isdigit():
        raw_name = f"output_{raw_name}"
    if len(raw_name) <= 64:
        return raw_name
    suffix = hashlib.sha256(raw_name.encode("utf-8")).hexdigest()[:8]
    return f"{raw_name[:55]}_{suffix}"


_ARRAY_ROOT_FIELD_CANDIDATES = (
    "cards",
    "review_points",
    "items",
    "results",
    "data",
    "recommendations",
    "suggestions",
    "points",
)
# 模型常见字段别名 → schema 规范字段名（仅当规范字段缺失时回填）。
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "generated_content": (
        "response_draft",
        "draft",
        "draft_content",
        "generated_draft",
        "reply",
        "reply_text",
        "reply_content",
        "content",
        "text",
        "body",
    ),
    "used_fact_ids": (
        "fact_ids",
        "used_facts",
        "linked_fact_ids",
        "cited_fact_ids",
        "used_fact_id",
    ),
    "content": (
        "text",
        "body",
        "message",
        "reply",
    ),
    # analysis / action 侧常见别名
    "expected_outputs": (
        "expected_output",
        "outputs",
        "deliverables",
        "expected_results",
    ),
    "recommendation_basis": (
        "basis",
        "rationale",
        "reasons",
        "justification",
    ),
    "addressed_concern": (
        "concern",
        "target_concern",
        "reviewer_concern",
    ),
    "classification_reason": (
        "reason",
        "rationale",
        "explanation",
        "summary",
    ),
    "assessment_reason": (
        "reason",
        "rationale",
        "explanation",
        "summary",
    ),
    "secondary_types": ("secondary_type", "other_types"),
    "candidate_types": ("candidates", "candidate_type"),
    "risk_signals": ("risks", "risk_flags", "risk_notes"),
    "alternative_actions": ("alternatives", "alternative"),
    "required_facts": ("facts_required", "needed_facts"),
    "prerequisites": ("deps", "requirements"),
}
_STRUCTURE_ROOT_REMINDER = (
    "请只输出一个匹配 schema 的 JSON 对象根节点（type=object），"
    "不要输出顶层数组，不要使用 Markdown 代码块，不要附加解释。"
)


def _json_instruction(transport_schema: dict[str, Any]) -> dict[str, str]:
    serialized_schema = json.dumps(
        transport_schema,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "role": "user",
        "content": (
            f"{_STRUCTURE_ROOT_REMINDER}"
            "该对象必须匹配以下 JSON Schema：\n"
            f"{serialized_schema}"
        ),
    }


def _message_text(message: Any) -> Any:
    content = _get_value(message, "content")
    if not isinstance(content, list):
        return content

    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
            continue
        part_type = str(_get_value(part, "type", "") or "").casefold()
        # Anthropic / 部分中转会把 thinking 与最终文本拆成多段 content block。
        if part_type in {
            "thinking",
            "reasoning",
            "redacted_thinking",
            "redacted_reasoning",
        }:
            continue
        text = _get_value(part, "text")
        if isinstance(text, str) and text.strip():
            parts.append(text)
            continue
    return "".join(parts)


_THINK_BLOCK_RE = re.compile(
    r"<\s*(?:think|thinking|reason|reasoning|redacted_reasoning)\b[^>]*>"
    r".*?"
    r"<\s*/\s*(?:think|thinking|reason|reasoning|redacted_reasoning)\s*>",
    re.DOTALL | re.IGNORECASE,
)
_THINK_TAG_RE = re.compile(
    r"<\s*/?\s*(?:think|thinking|reason|reasoning|redacted_reasoning)\b[^>]*>",
    re.IGNORECASE,
)


def _strip_reasoning_noise(text: str) -> str:
    """去掉 thinking/reasoning 包裹，适配无法关闭思考模式的自建服务。"""
    cleaned = _THINK_BLOCK_RE.sub("", text)
    cleaned = _THINK_TAG_RE.sub("", cleaned)
    return cleaned.strip()


def _extract_json_candidate_text(text: str) -> str:
    """从模型文本中提取最可能的 JSON 片段（含 markdown 代码块）。"""
    stripped = _strip_reasoning_noise(text)
    if not stripped:
        return ""
    fenced = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        stripped,
        re.DOTALL | re.IGNORECASE,
    )
    if fenced:
        return fenced.group(1).strip()
    embedded = re.search(
        r"```(?:json)?\s*(.*?)\s*```",
        stripped,
        re.DOTALL | re.IGNORECASE,
    )
    if embedded:
        return embedded.group(1).strip()
    return stripped


def _annotation_is_list(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin is list:
        return True
    if origin is None:
        return annotation is list
    if origin is Union or origin is getattr(types, "UnionType", ()):
        return any(
            arg is not type(None) and _annotation_is_list(arg) for arg in get_args(annotation)
        )
    return False


def _list_field_names(schema: type[BaseModel]) -> list[str]:
    return [
        name
        for name, field_info in schema.model_fields.items()
        if _annotation_is_list(field_info.annotation)
    ]


def _normalize_alias_value(canonical: str, value: Any) -> Any:
    """把别名字段值整理成规范字段可用形态。"""
    if canonical == "generated_content" and isinstance(value, dict):
        for key in (
            "generated_content",
            "content",
            "text",
            "body",
            "draft",
            "response_draft",
        ):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested
    list_like = {
        "used_fact_ids",
        "expected_outputs",
        "recommendation_basis",
        "secondary_types",
        "candidate_types",
        "risk_signals",
        "alternative_actions",
        "required_facts",
        "prerequisites",
    }
    if canonical in list_like:
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        if isinstance(value, dict):
            for key in (canonical, "ids", "items", "values"):
                nested = value.get(key)
                if isinstance(nested, list):
                    return nested
    return value


def _apply_field_aliases(
    payload: dict[str, Any],
    schema: type[BaseModel],
) -> dict[str, Any]:
    """规范字段缺失时，用常见别名回填。"""
    result = dict(payload)
    for canonical, aliases in _FIELD_ALIASES.items():
        if canonical not in schema.model_fields:
            continue
        current = result.get(canonical)
        if current not in (None, "", [], {}):
            continue
        for alias in aliases:
            if alias not in result:
                continue
            value = _normalize_alias_value(canonical, result[alias])
            if value not in (None, "", [], {}):
                result[canonical] = value
                break
    return result


def _strip_unknown_fields(
    payload: dict[str, Any],
    schema: type[BaseModel],
) -> dict[str, Any]:
    """extra=forbid 时丢弃未知键，避免模型多写 source_id 等字段直接校验失败。"""
    extra = schema.model_config.get("extra")
    extra_value = getattr(extra, "value", extra)
    if extra_value != "forbid":
        return payload
    allowed = set(schema.model_fields)
    return {key: value for key, value in payload.items() if key in allowed}


def _unwrap_annotation(annotation: Any) -> Any:
    """剥掉 Optional / Annotated 外壳。"""
    origin = get_origin(annotation)
    if origin is Annotated:
        args = get_args(annotation)
        return _unwrap_annotation(args[0]) if args else annotation
    if origin is Union or origin is getattr(types, "UnionType", ()):
        non_none = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(non_none) == 1:
            return _unwrap_annotation(non_none[0])
    return annotation


def _model_type_from_annotation(annotation: Any) -> type[BaseModel] | None:
    unwrapped = _unwrap_annotation(annotation)
    if isinstance(unwrapped, type) and issubclass(unwrapped, BaseModel):
        return unwrapped
    return None


def _list_item_model_from_annotation(annotation: Any) -> type[BaseModel] | None:
    unwrapped = _unwrap_annotation(annotation)
    origin = get_origin(unwrapped)
    if origin is list:
        args = get_args(unwrapped)
        if not args:
            return None
        return _model_type_from_annotation(args[0])
    return None


def _coerce_list_scalars(payload: dict[str, Any], schema: type[BaseModel]) -> dict[str, Any]:
    """列表字段若收到单个标量字符串，包成单元素列表。"""
    result = dict(payload)
    for name, field_info in schema.model_fields.items():
        if name not in result:
            continue
        if not _annotation_is_list(field_info.annotation):
            continue
        value = result[name]
        if isinstance(value, str):
            text = value.strip()
            result[name] = [text] if text else []
    return result


def _coerce_payload_for_schema(payload: Any, schema: type[BaseModel]) -> Any:
    """兼容模型常见变形：顶层数组、单键包装、字段别名、多余字段、嵌套对象。"""
    if isinstance(payload, list):
        list_fields = _list_field_names(schema)
        if len(list_fields) == 1:
            return _coerce_payload_for_schema({list_fields[0]: payload}, schema)
        for name in _ARRAY_ROOT_FIELD_CANDIDATES:
            if name in schema.model_fields and name in list_fields:
                return _coerce_payload_for_schema({name: payload}, schema)
        return payload

    if isinstance(payload, dict):
        if len(payload) == 1:
            key, value = next(iter(payload.items()))
            if key in {
                schema.__name__,
                "data",
                "result",
                "output",
                "response",
                "response_draft",
                "draft",
            } and isinstance(value, (dict, list)):
                return _coerce_payload_for_schema(value, schema)
            # {"response_draft": "正文..."} → generated_content
            if (
                "generated_content" in schema.model_fields
                and key
                in {
                    "response_draft",
                    "draft",
                    "content",
                    "text",
                    "body",
                    "reply",
                }
                and isinstance(value, str)
            ):
                payload = {"generated_content": value}
        payload = _apply_field_aliases(payload, schema)
        payload = _coerce_list_scalars(payload, schema)
        payload = _strip_unknown_fields(payload, schema)
        # 递归处理嵌套 BaseModel / list[BaseModel]，否则子对象 extra 字段仍会炸
        coerced: dict[str, Any] = dict(payload)
        for name, field_info in schema.model_fields.items():
            if name not in coerced:
                continue
            value = coerced[name]
            nested_model = _model_type_from_annotation(field_info.annotation)
            item_model = _list_item_model_from_annotation(field_info.annotation)
            if nested_model is not None and isinstance(value, dict):
                coerced[name] = _coerce_payload_for_schema(value, nested_model)
            elif item_model is not None and isinstance(value, list):
                coerced[name] = [
                    _coerce_payload_for_schema(item, item_model)
                    if isinstance(item, dict)
                    else item
                    for item in value
                ]
        return coerced

    return payload


def _parse_json_content(content: Any) -> Any:
    if isinstance(content, (dict, list)):
        return content
    if not isinstance(content, str) or not content.strip():
        raise StructuredOutputParseError("模型未返回可解析的 JSON 内容。")

    stripped = _extract_json_candidate_text(content)
    if not stripped:
        raise StructuredOutputParseError("模型未返回可解析的 JSON 内容。")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as first_error:
        positions = [index for index in (stripped.find("{"), stripped.find("[")) if index >= 0]
        if positions:
            try:
                parsed, _end = json.JSONDecoder().raw_decode(stripped[min(positions) :])
                return parsed
            except json.JSONDecodeError:
                pass
        raise StructuredOutputParseError(
            f"模型返回内容不是合法 JSON：line={first_error.lineno}, "
            f"column={first_error.colno}。"
        ) from first_error


def _choice_message(response: Any) -> Any:
    choices = _get_value(response, "choices", [])
    if not choices:
        raise StructuredOutputParseError("Chat Completions 响应不包含 choices。")
    message = _get_value(choices[0], "message")
    if message is None:
        raise StructuredOutputParseError("Chat Completions 响应不包含 message。")
    if _get_value(message, "refusal"):
        raise StructuredOutputParseError("模型拒绝生成结构化结果。")
    return message


def _tool_arguments(message: Any, expected_name: str) -> Any:
    tool_calls = _get_value(message, "tool_calls", []) or []
    observed_names: list[str] = []
    for tool_call in tool_calls:
        function = _get_value(tool_call, "function", {})
        name = _get_value(function, "name", "")
        if name:
            observed_names.append(str(name))
        if name == expected_name:
            arguments = _get_value(function, "arguments")
            return _parse_json_content(arguments)

    legacy_call = _get_value(message, "function_call")
    if legacy_call:
        name = _get_value(legacy_call, "name", "")
        if name:
            observed_names.append(str(name))
        if name == expected_name:
            return _parse_json_content(_get_value(legacy_call, "arguments"))

    if observed_names:
        raise StructuredOutputParseError(
            "模型调用了错误的工具；"
            f"expected={expected_name!r}, observed={observed_names!r}。"
        )
    raise StructuredOutputParseError(
        f"模型未调用目标工具；expected={expected_name!r}。"
    )


def _extract_payload(
    response: Any,
    strategy: StructuredOutputStrategy,
    tool_name: str,
) -> Any:
    message = _choice_message(response)
    if strategy is StructuredOutputStrategy.FORCED_TOOL:
        try:
            return _tool_arguments(message, tool_name)
        except StructuredOutputParseError as tool_error:
            # 自建模型常忽略 tool_choice，把 JSON 放在 content 里
            text = _message_text(message)
            if isinstance(text, str) and text.strip():
                try:
                    return _parse_json_content(text)
                except StructuredOutputParseError:
                    pass
            raise tool_error
    if strategy is StructuredOutputStrategy.UNFORCED_TOOL:
        tool_calls = _get_value(message, "tool_calls", []) or []
        if tool_calls or _get_value(message, "function_call"):
            return _tool_arguments(message, tool_name)
    return _parse_json_content(_message_text(message))


def _safe_pydantic_errors(error: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "type": item.get("type"),
            "loc": list(item.get("loc", ())),
            "msg": item.get("msg"),
        }
        for item in error.errors(include_url=False, include_input=False)
    ]


def _validation_retry_message(details: Any) -> dict[str, str]:
    return {
        "role": "user",
        "content": (
            "上一次结构化输出校验失败。请重新生成完整结果，并严格修正以下错误。\n"
            f"{_STRUCTURE_ROOT_REMINDER}\n"
            "错误详情：\n"
            + json.dumps(details, ensure_ascii=False, default=str)
        ),
    }


def _error_body(error: Exception) -> Mapping[str, Any]:
    body = getattr(error, "body", None)
    if not isinstance(body, Mapping):
        return {}
    nested = body.get("error")
    return nested if isinstance(nested, Mapping) else body


def _unsupported_parameter(
    error: Exception,
    strategy: StructuredOutputStrategy,
) -> _UnsupportedParameter | None:
    if getattr(error, "status_code", None) != 400:
        return None

    relevant_parameters = {
        StructuredOutputStrategy.NATIVE_JSON_SCHEMA: {"response_format"},
        StructuredOutputStrategy.FORCED_TOOL: {"tools", "tool_choice"},
        StructuredOutputStrategy.UNFORCED_TOOL: {"tools"},
        StructuredOutputStrategy.JSON_OBJECT: {"response_format"},
        StructuredOutputStrategy.PROMPT_JSON: set(),
    }[strategy]
    if not relevant_parameters:
        return None

    body = _error_body(error)
    code = str(body.get("code", getattr(error, "code", "")) or "").casefold()
    parameter = str(body.get("param", getattr(error, "param", "")) or "")
    message = str(body.get("message", getattr(error, "message", str(error))) or "")
    lowered_message = message.casefold()
    explicit_codes = {
        "not_supported",
        "unknown_parameter",
        "unsupported_parameter",
        "unsupported_value",
    }
    explicit_markers = (
        "unsupported parameter",
        "unknown parameter",
        "unrecognized parameter",
        "unrecognized request argument",
        "unexpected keyword argument",
        "not supported",
        "does not support",
        "unavailable",
        "is unavailable",
        "not available",
        "不支持参数",
        "参数不受支持",
        "不允许的参数",
        "未知参数",
        "无法识别参数",
        "不可用",
    )
    if code not in explicit_codes and not any(
        marker in lowered_message for marker in explicit_markers
    ):
        return None

    normalized_parameter = parameter.casefold().replace("[", ".").replace("]", "")
    root_parameter = normalized_parameter.split(".", 1)[0]
    if root_parameter not in relevant_parameters:
        mentioned = next(
            (
                item
                for item in relevant_parameters
                if re.search(rf"\b{re.escape(item)}\b", lowered_message)
            ),
            None,
        )
        if mentioned is None:
            return None
        root_parameter = mentioned
        normalized_parameter = parameter or mentioned

    safe_parameter = normalized_parameter or root_parameter
    if safe_parameter == "response_format" or safe_parameter.startswith("response_format"):
        if "json_schema" in lowered_message or "json schema" in lowered_message:
            safe_parameter = "response_format.json_schema"
        elif "json_object" in lowered_message or "json object" in lowered_message:
            safe_parameter = "response_format.json_object"
        elif "unavailable" in lowered_message or "不可用" in lowered_message:
            # type 不可用时通常仅 json_schema 失效，勿连 json_object 一起禁用
            safe_parameter = "response_format.json_schema"
    return _UnsupportedParameter(
        parameter=safe_parameter,
        reason=f"unsupported_parameter:param={safe_parameter}",
    )


class StructuredOutputEngine:
    """Negotiate structured-output capabilities without provider/model branches."""

    def __init__(
        self,
        *,
        client_factory: ClientFactory = _default_client_factory,
        sleep: Callable[[float], None] = time.sleep,
        base_delay: float = 0.5,
        strategy_order: Sequence[str | StructuredOutputStrategy] = _DEFAULT_STRATEGY_ORDER,
        max_retries_by_purpose: Mapping[str, int] | None = None,
        logger: logging.Logger = _LOGGER,
    ) -> None:
        self._client_factory = client_factory
        self._sleep = sleep
        if base_delay < 0:
            raise ValueError("base_delay 不能小于 0。")
        self._base_delay = base_delay
        self._strategy_order = tuple(
            item
            if isinstance(item, StructuredOutputStrategy)
            else StructuredOutputStrategy(item)
            for item in strategy_order
        )
        self._max_retries_by_purpose = dict(max_retries_by_purpose or {})
        self._logger = logger
        self._cache: dict[_CapabilityKey, _CapabilityRecord] = {}
        self._cache_lock = RLock()
        fingerprint_payload = {
            "strategies": [item.value for item in self._strategy_order],
            "transport_schema_version": _TRANSPORT_SCHEMA_VERSION,
            "extra_body": _llm_extra_body(),
        }
        self._config_fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]

    def _max_retries(self, purpose: str) -> int:
        if purpose in self._max_retries_by_purpose:
            return self._max_retries_by_purpose[purpose]
        # paper_card：不重试；draft（回复草稿等）：最多再试 1 次，避免格式失败拖慢并发。
        if purpose == "paper_card":
            return 0
        if purpose == "draft":
            return 1
        return 3

    @staticmethod
    def _resolve_timeout(purpose: str, timeout_seconds: float | None) -> float:
        resolved = timeout_seconds
        if resolved is None:
            settings = get_settings()
            resolved = (
                settings.PAPER_CARD_LLM_TIMEOUT_SECONDS
                if purpose == "paper_card"
                else settings.LLM_TIMEOUT_SECONDS
            )
        if resolved <= 0:
            raise ValueError("timeout_seconds 必须大于 0。")
        return resolved

    def _cache_key(self, base_url: str, model_name: str) -> _CapabilityKey:
        return _CapabilityKey(
            base_url=base_url.rstrip("/"),
            model_name=model_name,
            config_fingerprint=self._config_fingerprint,
        )

    def _record_snapshot(self, key: _CapabilityKey) -> _CapabilityRecord:
        with self._cache_lock:
            record = self._cache.get(key)
            if record is None:
                return _CapabilityRecord()
            return _CapabilityRecord(
                selected_strategy=record.selected_strategy,
                unsupported=dict(record.unsupported),
            )

    def _mark_supported(
        self,
        key: _CapabilityKey,
        strategy: StructuredOutputStrategy,
    ) -> None:
        with self._cache_lock:
            record = self._cache.setdefault(key, _CapabilityRecord())
            record.selected_strategy = strategy
            record.unsupported.pop(strategy, None)

    def _mark_unsupported(
        self,
        key: _CapabilityKey,
        strategy: StructuredOutputStrategy,
        unsupported: _UnsupportedParameter,
    ) -> None:
        affected = {strategy}
        root_parameter = unsupported.parameter.split(".", 1)[0]
        if root_parameter == "tools":
            # tools 整体不可用时，forced / unforced 一并失效
            affected.update(
                {
                    StructuredOutputStrategy.FORCED_TOOL,
                    StructuredOutputStrategy.UNFORCED_TOOL,
                }
            )
        elif root_parameter == "tool_choice":
            # 仅 tool_choice 被忽略/不可用时，仍可尝试 unforced_tool
            affected.add(StructuredOutputStrategy.FORCED_TOOL)
        elif root_parameter == "response_format" and unsupported.parameter == "response_format":
            affected.update(
                {
                    StructuredOutputStrategy.NATIVE_JSON_SCHEMA,
                    StructuredOutputStrategy.JSON_OBJECT,
                }
            )
        with self._cache_lock:
            record = self._cache.setdefault(key, _CapabilityRecord())
            for item in affected:
                record.unsupported[item] = unsupported.reason
                if record.selected_strategy is item:
                    record.selected_strategy = None

    def _candidate_strategies(self, key: _CapabilityKey) -> list[StructuredOutputStrategy]:
        record = self._record_snapshot(key)
        candidates: list[StructuredOutputStrategy] = []
        if (
            record.selected_strategy is not None
            and record.selected_strategy not in record.unsupported
        ):
            candidates.append(record.selected_strategy)
        candidates.extend(
            strategy
            for strategy in self._strategy_order
            if strategy not in candidates and strategy not in record.unsupported
        )
        return candidates

    def _request_kwargs(
        self,
        *,
        strategy: StructuredOutputStrategy,
        model_name: str,
        messages: list[dict[str, Any]],
        transport_schema: dict[str, Any],
        tool_name: str,
    ) -> dict[str, Any]:
        request_messages = list(messages)
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": request_messages,
        }
        if strategy is StructuredOutputStrategy.NATIVE_JSON_SCHEMA:
            # 不启用 strict：可选字段（如 confidence 默认值）与 OpenAI strict 的
            # “全部 properties 必须 required”冲突，会直接 400。
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": tool_name,
                    "schema": transport_schema,
                },
            }
            # 即便渠道支持 json_schema，也再强调一次根节点必须是 object。
            request_messages.append(
                {
                    "role": "user",
                    "content": _STRUCTURE_ROOT_REMINDER,
                }
            )
        elif strategy in {
            StructuredOutputStrategy.FORCED_TOOL,
            StructuredOutputStrategy.UNFORCED_TOOL,
        }:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": (
                            "Return the requested structured result as one function call. "
                            "Arguments must be a single JSON object matching the schema root "
                            "(never a bare top-level array)."
                        ),
                        "parameters": transport_schema,
                    },
                }
            ]
            if strategy is StructuredOutputStrategy.FORCED_TOOL:
                kwargs["tool_choice"] = {
                    "type": "function",
                    "function": {"name": tool_name},
                }
            request_messages.append(
                {
                    "role": "user",
                    "content": (
                        "请通过函数调用返回结果；arguments 必须是匹配 schema 的 JSON 对象，"
                        "不能是顶层数组。"
                    ),
                }
            )
        elif strategy is StructuredOutputStrategy.JSON_OBJECT:
            request_messages.append(_json_instruction(transport_schema))
            kwargs["response_format"] = {"type": "json_object"}
        else:
            request_messages.append(_json_instruction(transport_schema))
        extra_body = _llm_extra_body()
        if extra_body:
            # 合并到 extra_body，避免覆盖 strategy 已设置的顶层字段
            existing = kwargs.get("extra_body")
            if isinstance(existing, dict):
                merged = {**extra_body, **existing}
            else:
                merged = dict(extra_body)
            kwargs["extra_body"] = merged
        return kwargs

    def _invalidate_strategy_after_payload_failure(
        self,
        *,
        key: _CapabilityKey,
        strategy: StructuredOutputStrategy,
        endpoint_host: str,
        model_name: str,
        tool_name: str,
        cache_hit: bool,
        detail: str,
    ) -> str:
        """HTTP 成功但载荷不可用时，标记策略失效并缓存降级。"""
        if strategy is StructuredOutputStrategy.NATIVE_JSON_SCHEMA:
            parameter = "response_format.json_schema"
        elif strategy is StructuredOutputStrategy.FORCED_TOOL:
            parameter = "tool_choice"
        elif strategy is StructuredOutputStrategy.UNFORCED_TOOL:
            parameter = "tools"
        elif strategy is StructuredOutputStrategy.JSON_OBJECT:
            parameter = "response_format.json_object"
        else:
            parameter = strategy.value
        reason = f"accepted_but_invalid_payload:{detail}"
        unsupported = _UnsupportedParameter(parameter=parameter, reason=reason)
        self._mark_unsupported(key, strategy, unsupported)
        self._logger.warning(
            "STRUCTURED_OUTPUT_DOWNGRADE model=%s endpoint_host=%s "
            "selected_strategy=%s capability_cache_hit=%s "
            "downgrade_reason=%s tool_name=%s pydantic_errors=none",
            model_name,
            endpoint_host,
            strategy.value,
            str(cache_hit).lower(),
            reason,
            tool_name,
        )
        return reason

    def _send_with_negotiation(
        self,
        *,
        client: Any,
        key: _CapabilityKey,
        model_name: str,
        messages: list[dict[str, Any]],
        transport_schema: dict[str, Any],
        tool_name: str,
        endpoint_host: str,
        cache_hit: bool,
    ) -> tuple[Any, StructuredOutputStrategy, str]:
        downgrade_reason = "none"
        while True:
            candidates = self._candidate_strategies(key)
            if not candidates:
                raise LlmFinalError("没有可用的结构化输出策略。")
            strategy = candidates[0]
            kwargs = self._request_kwargs(
                strategy=strategy,
                model_name=model_name,
                messages=messages,
                transport_schema=transport_schema,
                tool_name=tool_name,
            )
            try:
                response = client.chat.completions.create(**kwargs)
            except Exception as error:
                unsupported = _unsupported_parameter(error, strategy)
                if unsupported is None:
                    raise
                downgrade_reason = unsupported.reason
                self._mark_unsupported(key, strategy, unsupported)
                self._logger.warning(
                    "STRUCTURED_OUTPUT_DOWNGRADE model=%s endpoint_host=%s "
                    "selected_strategy=%s capability_cache_hit=%s "
                    "downgrade_reason=%s tool_name=%s pydantic_errors=none",
                    model_name,
                    endpoint_host,
                    strategy.value,
                    str(cache_hit).lower(),
                    downgrade_reason,
                    tool_name,
                )
                continue
            # 仅 HTTP 成功不代表策略可用；等解析/校验通过后再 mark_supported。
            return response, strategy, downgrade_reason

    def invoke_structured(
        self,
        purpose: str,
        schema: type[SchemaT],
        messages: Sequence[Any],
        *,
        timeout_seconds: float | None = None,
    ) -> SchemaT:
        """Invoke one configured model and return a locally validated schema instance."""
        if not isinstance(schema, type) or not issubclass(schema, BaseModel):
            raise TypeError("schema 必须是 Pydantic BaseModel 子类。")
        settings = get_settings()
        settings.require_llm()
        model_name = _model_for(purpose)
        timeout_seconds = self._resolve_timeout(purpose, timeout_seconds)

        base_url = settings.LLM_BASE_URL
        endpoint_host = urlparse(base_url).hostname or urlparse(base_url).netloc or "unknown"
        key = self._cache_key(base_url, model_name)
        initial_record = self._record_snapshot(key)
        cache_hit = bool(initial_record.selected_strategy or initial_record.unsupported)
        normalized_messages = _normalize_messages(messages)
        current_messages = list(normalized_messages)
        transport_schema = _transport_schema(schema)
        tool_name = _tool_name(schema)
        client = self._client_factory(
            base_url=base_url,
            api_key=settings.LLM_API_KEY,
            timeout=timeout_seconds,
            max_retries=0,
        )
        max_retries = self._max_retries(purpose)
        if max_retries < 0:
            raise ValueError("max_retries 不能小于 0。")

        last_validation_details: Any = None
        last_validation_error: Exception | None = None
        for attempt_index in range(max_retries + 1):
            strategy: StructuredOutputStrategy | None = None
            downgrade_reason = "none"
            try:
                # 同一 attempt 内允许因“假 json_schema”继续降级策略
                while True:
                    response, strategy, downgrade_reason = self._send_with_negotiation(
                        client=client,
                        key=key,
                        model_name=model_name,
                        messages=current_messages,
                        transport_schema=transport_schema,
                        tool_name=tool_name,
                        endpoint_host=endpoint_host,
                        cache_hit=cache_hit,
                    )
                    try:
                        payload = _extract_payload(response, strategy, tool_name)
                        payload = _coerce_payload_for_schema(payload, schema)
                        result = schema.model_validate(payload)
                    except StructuredOutputParseError as parse_error:
                        # 假 json_schema / 忽略 tool_choice / 假 json_object 等：降级换策略
                        if strategy in {
                            StructuredOutputStrategy.NATIVE_JSON_SCHEMA,
                            StructuredOutputStrategy.FORCED_TOOL,
                            StructuredOutputStrategy.JSON_OBJECT,
                        }:
                            detail = "parse_error"
                            message = str(parse_error)
                            if "未调用目标工具" in message:
                                detail = "tool_not_called"
                            elif "错误的工具" in message:
                                detail = "wrong_tool"
                            downgrade_reason = self._invalidate_strategy_after_payload_failure(
                                key=key,
                                strategy=strategy,
                                endpoint_host=endpoint_host,
                                model_name=model_name,
                                tool_name=tool_name,
                                cache_hit=cache_hit,
                                detail=detail,
                            )
                            continue
                        raise parse_error
                    except ValidationError:
                        # JSON 已可解析但字段不合规：说明通道可用，留给本 attempt 的重试提示。
                        raise
                    else:
                        self._mark_supported(key, strategy)
                        break
            except ValidationError as error:
                last_validation_error = error
                last_validation_details = _safe_pydantic_errors(error)
                selected = strategy or self._record_snapshot(key).selected_strategy
                # 中间重试只打 debug，避免接入方控制台被刷屏；最终失败另有 warning。
                self._logger.debug(
                    "STRUCTURED_OUTPUT_VALIDATION model=%s endpoint_host=%s "
                    "selected_strategy=%s capability_cache_hit=%s "
                    "downgrade_reason=%s tool_name=%s attempt=%s/%s pydantic_errors=%s",
                    model_name,
                    endpoint_host,
                    selected.value if selected is not None else "none",
                    str(cache_hit).lower(),
                    downgrade_reason,
                    tool_name,
                    attempt_index + 1,
                    max_retries + 1,
                    json.dumps(last_validation_details, ensure_ascii=False),
                )
            except StructuredOutputParseError as error:
                last_validation_error = error
                last_validation_details = [{"type": "parse_error", "msg": str(error)}]
                selected = strategy or self._record_snapshot(key).selected_strategy
                self._logger.debug(
                    "STRUCTURED_OUTPUT_VALIDATION model=%s endpoint_host=%s "
                    "selected_strategy=%s capability_cache_hit=%s "
                    "downgrade_reason=%s tool_name=%s attempt=%s/%s pydantic_errors=%s",
                    model_name,
                    endpoint_host,
                    selected.value if selected is not None else "none",
                    str(cache_hit).lower(),
                    downgrade_reason,
                    tool_name,
                    attempt_index + 1,
                    max_retries + 1,
                    json.dumps(last_validation_details, ensure_ascii=False),
                )
            except Exception as error:
                if is_retryable_llm_error(error):
                    if attempt_index >= max_retries:
                        raise LlmRetryableError(
                            f"LLM 临时错误在 {max_retries + 1} 次调用后仍未恢复。",
                            cause=error,
                        ) from error
                else:
                    raise LlmFinalError(
                        f"LLM 调用发生不可重试错误：{type(error).__name__}。",
                        cause=error,
                    ) from error
            else:
                self._logger.info(
                    "STRUCTURED_OUTPUT_SUCCESS model=%s endpoint_host=%s "
                    "selected_strategy=%s capability_cache_hit=%s "
                    "downgrade_reason=%s tool_name=%s pydantic_errors=none",
                    model_name,
                    endpoint_host,
                    strategy.value if strategy is not None else "none",
                    str(cache_hit).lower(),
                    downgrade_reason,
                    tool_name,
                )
                return result

            if attempt_index < max_retries:
                if last_validation_details is not None:
                    current_messages = [
                        *current_messages,
                        _validation_retry_message(last_validation_details),
                    ]
                    last_validation_details = None
                self._sleep(self._base_delay * (2**attempt_index))

        details = last_validation_details or [{"type": "validation_error"}]
        self._logger.warning(
            "STRUCTURED_OUTPUT_FAILED model=%s endpoint_host=%s tool_name=%s "
            "attempts=%s pydantic_errors=%s",
            model_name,
            endpoint_host,
            tool_name,
            max_retries + 1,
            json.dumps(details, ensure_ascii=False, default=str),
        )
        raise LlmFinalError(
            f"LLM 结构化输出在 {max_retries + 1} 次调用后仍未通过校验："
            f"{json.dumps(details, ensure_ascii=False, default=str)}",
            cause=last_validation_error,
        ) from last_validation_error

    def probe_capabilities(
        self,
        purpose: str,
        schema: type[SchemaT],
        messages: Sequence[Any],
        *,
        timeout_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        """Probe every request shape once with non-business data."""
        if not isinstance(schema, type) or not issubclass(schema, BaseModel):
            raise TypeError("schema 必须是 Pydantic BaseModel 子类。")
        settings = get_settings()
        settings.require_llm()
        model_name = _model_for(purpose)
        timeout_seconds = self._resolve_timeout(purpose, timeout_seconds)
        base_url = settings.LLM_BASE_URL
        endpoint_host = urlparse(base_url).hostname or urlparse(base_url).netloc or "unknown"
        key = self._cache_key(base_url, model_name)
        normalized_messages = _normalize_messages(messages)
        transport_schema = _transport_schema(schema)
        tool_name = _tool_name(schema)
        client = self._client_factory(
            base_url=base_url,
            api_key=settings.LLM_API_KEY,
            timeout=timeout_seconds,
            max_retries=0,
        )
        matrix: list[dict[str, Any]] = []
        first_accepted: StructuredOutputStrategy | None = None

        for strategy in self._strategy_order:
            kwargs = self._request_kwargs(
                strategy=strategy,
                model_name=model_name,
                messages=normalized_messages,
                transport_schema=transport_schema,
                tool_name=tool_name,
            )
            try:
                response = client.chat.completions.create(**kwargs)
            except Exception as error:
                unsupported = _unsupported_parameter(error, strategy)
                if unsupported is not None:
                    status = "unsupported"
                    detail = unsupported.reason
                    self._mark_unsupported(key, strategy, unsupported)
                elif is_retryable_llm_error(error):
                    status = "temporary_error"
                    status_code = getattr(error, "status_code", None)
                    detail = f"{type(error).__name__}:http={status_code or 'none'}"
                else:
                    status = "error"
                    status_code = getattr(error, "status_code", None)
                    detail = f"{type(error).__name__}:http={status_code or 'none'}"
            else:
                if first_accepted is None:
                    first_accepted = strategy
                try:
                    payload = _extract_payload(response, strategy, tool_name)
                    payload = _coerce_payload_for_schema(payload, schema)
                    schema.model_validate(payload)
                except ValidationError as error:
                    status = "accepted_invalid"
                    detail = json.dumps(
                        _safe_pydantic_errors(error),
                        ensure_ascii=False,
                    )
                except StructuredOutputParseError as error:
                    status = "accepted_invalid"
                    detail = str(error)
                else:
                    status = "supported"
                    detail = "validated"

            matrix.append(
                {
                    "purpose": purpose,
                    "model": model_name,
                    "endpoint_host": endpoint_host,
                    "strategy": strategy.value,
                    "status": status,
                    "detail": detail,
                    "tool_name": tool_name,
                }
            )
            pydantic_errors = (
                detail if status == "accepted_invalid" else "none"
            )
            self._logger.info(
                "STRUCTURED_OUTPUT_PROBE model=%s endpoint_host=%s "
                "selected_strategy=%s capability_cache_hit=false "
                "downgrade_reason=%s tool_name=%s pydantic_errors=%s "
                "probe_status=%s",
                model_name,
                endpoint_host,
                strategy.value,
                detail if status == "unsupported" else "none",
                tool_name,
                pydantic_errors,
                status,
            )

        if first_accepted is not None:
            self._mark_supported(key, first_accepted)
        return matrix


_DEFAULT_ENGINE = StructuredOutputEngine()


def invoke_structured(
    purpose: str,
    schema: type[SchemaT],
    messages: Sequence[Any],
    *,
    timeout_seconds: float | None = None,
) -> SchemaT:
    """The only public structured-output invocation interface."""
    return _DEFAULT_ENGINE.invoke_structured(
        purpose,
        schema,
        messages,
        timeout_seconds=timeout_seconds,
    )


__all__ = ["invoke_structured"]
