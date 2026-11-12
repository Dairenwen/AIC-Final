"""图 invoke / resume 运行时：统一把 LangGraph 输出转成 AgentResult。

参考 backend WorkspaceTaskRunner / SourceReplyRunner / SuggestionAnalysisRunner
的 interrupt 解析逻辑，但不引入 ThreadPoolTaskExecutor 或 Flask。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping
from uuid import UUID, uuid4

from langgraph.types import Command, StateSnapshot

from langgraph_agent.schemas.interaction import PendingInteraction, ResumeCommand
from langgraph_agent.schemas.public_api import AgentResult, AgentStatus
from langgraph_agent.schemas.run import GraphRunStatus, ResultReference
from langgraph_agent.utils.exceptions import AgentError, InvalidInput


class GraphKind(str, Enum):
    """门面调度用的四条业务图标识。"""

    TASK_INIT = "TASK_INIT"
    ANALYSIS = "ANALYSIS"
    REPLY = "REPLY"
    FINALIZE = "FINALIZE"


def thread_config(thread_id: str) -> dict[str, dict[str, str]]:
    """LangGraph configurable 线程配置。"""
    return {"configurable": {"thread_id": thread_id}}


def build_task_init_thread_id(workspace_id: UUID, run_id: UUID) -> str:
    return f"workspace:{workspace_id}:task:{run_id}"


def build_analysis_thread_id(
    workspace_id: UUID, suggestion_id: UUID, run_id: UUID
) -> str:
    return (
        f"workspace:{workspace_id}:suggestion:{suggestion_id}:analysis:{run_id}"
    )


def build_finalize_thread_id(workspace_id: UUID) -> str:
    return f"workspace:{workspace_id}:finalize"


def infer_graph_kind(thread_id: str) -> GraphKind:
    """从 thread_id 约定推断图类型（用于 resume 路由）。"""
    if ":finalize" in thread_id and thread_id.endswith(":finalize"):
        return GraphKind.FINALIZE
    if ":analysis:" in thread_id:
        return GraphKind.ANALYSIS
    if ":reply:" in thread_id:
        return GraphKind.REPLY
    if ":task:" in thread_id:
        return GraphKind.TASK_INIT
    raise InvalidInput(f"无法从 thread_id 推断图类型：{thread_id}")


def _interrupt_values(
    output: Mapping[str, Any] | None,
    snapshot: StateSnapshot | None,
) -> tuple[object, ...]:
    if output:
        raw = output.get("__interrupt__", ())
        if raw:
            return tuple(item.value for item in raw)
    if snapshot is not None:
        return tuple(item.value for item in snapshot.interrupts)
    return ()


def extract_pending_interaction(
    output: Mapping[str, Any] | None = None,
    snapshot: StateSnapshot | None = None,
    *,
    allow_empty: bool = True,
) -> PendingInteraction | None:
    """从 invoke 输出或 checkpoint snapshot 解析唯一 PendingInteraction。"""
    values = _interrupt_values(output, snapshot)
    if not values:
        if allow_empty:
            return None
        raise AgentError("期望存在 interrupt，但 checkpoint 中为空")
    if len(values) != 1:
        raise AgentError(f"图同时返回了 {len(values)} 个 interrupt，期望恰好 1 个")
    return PendingInteraction.model_validate(values[0])


def extract_result_refs(
    output: Mapping[str, Any] | None,
    snapshot: StateSnapshot | None = None,
) -> list[ResultReference]:
    """规范化 result_refs；兼容 str/UUID id。"""
    raw_refs: Any = None
    if output is not None:
        raw_refs = output.get("result_refs")
    if raw_refs is None and snapshot is not None and isinstance(snapshot.values, dict):
        raw_refs = snapshot.values.get("result_refs", [])
    if raw_refs is None:
        raw_refs = []
    if not isinstance(raw_refs, list):
        raise AgentError("图输出的 result_refs 不是数组")
    result: list[ResultReference] = []
    for item in raw_refs:
        if isinstance(item, ResultReference):
            result.append(item)
            continue
        if not isinstance(item, Mapping):
            raise AgentError("result_refs 元素必须是对象")
        result.append(
            ResultReference.model_validate(
                {"type": item["type"], "id": item["id"]}
            )
        )
    return result


def _coerce_run_id(
    output: Mapping[str, Any] | None,
    snapshot: StateSnapshot | None,
    fallback: UUID | None,
) -> UUID:
    raw: Any = None
    if output is not None:
        raw = output.get("run_id")
    if raw is None and snapshot is not None and isinstance(snapshot.values, dict):
        raw = snapshot.values.get("run_id")
    if raw is None:
        raw = fallback
    if raw is None:
        raise AgentError("无法确定 run_id")
    return UUID(str(raw))


def _coerce_status(
    *,
    pending: PendingInteraction | None,
    output: Mapping[str, Any] | None,
    snapshot: StateSnapshot | None,
    error: BaseException | None = None,
) -> AgentStatus:
    if error is not None:
        return AgentStatus.FAILED
    if pending is not None:
        return AgentStatus.WAITING_HUMAN
    raw_status: Any = None
    if output is not None:
        raw_status = output.get("status")
    if raw_status is None and snapshot is not None and isinstance(snapshot.values, dict):
        raw_status = snapshot.values.get("status")
    if raw_status is None:
        return AgentStatus.SUCCEEDED
    text = str(
        raw_status.value if isinstance(raw_status, GraphRunStatus) else raw_status
    )
    if text in {
        GraphRunStatus.FAILED_RETRYABLE.value,
        GraphRunStatus.FAILED_FINAL.value,
        "FAILED",
    }:
        return AgentStatus.FAILED
    if text == GraphRunStatus.WAITING_USER.value:
        return AgentStatus.WAITING_HUMAN
    if text == GraphRunStatus.RUNNING.value:
        # 正常结束时节点可能仍留 RUNNING；无 interrupt 则视为成功
        return AgentStatus.SUCCEEDED
    if text == GraphRunStatus.SUCCEEDED.value:
        return AgentStatus.SUCCEEDED
    return AgentStatus.SUCCEEDED


def _phase_of(
    output: Mapping[str, Any] | None,
    snapshot: StateSnapshot | None,
) -> str | None:
    raw = None
    if output is not None:
        raw = output.get("phase")
    if raw is None and snapshot is not None and isinstance(snapshot.values, dict):
        raw = snapshot.values.get("phase")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _error_code_of(
    output: Mapping[str, Any] | None,
    snapshot: StateSnapshot | None,
    error: BaseException | None,
) -> str | None:
    if error is not None:
        code = getattr(error, "code", None)
        if isinstance(code, str) and code.strip():
            return code.strip()
        return "AGENT_ERROR"
    raw = None
    if output is not None:
        raw = output.get("error_code")
    if raw is None and snapshot is not None and isinstance(snapshot.values, dict):
        raw = snapshot.values.get("error_code")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _artifacts_of(
    output: Mapping[str, Any] | None,
    snapshot: StateSnapshot | None,
) -> dict[str, Any]:
    """抽取可对外暴露的附加产物（draft_refs 中的 final_result 等）。"""
    values: Mapping[str, Any] | None = None
    if output is not None:
        values = output
    elif snapshot is not None and isinstance(snapshot.values, dict):
        values = snapshot.values
    if not values:
        return {}
    artifacts: dict[str, Any] = {}
    draft = values.get("draft_refs")
    if isinstance(draft, dict):
        if "final_result" in draft:
            artifacts["final_result"] = draft["final_result"]
        if "export_snapshot" in draft:
            artifacts["export_snapshot"] = draft["export_snapshot"]
        if "persisted_reply" in draft:
            artifacts["persisted_reply"] = draft["persisted_reply"]
    return artifacts


def to_agent_result(
    *,
    thread_id: str,
    output: Mapping[str, Any] | None = None,
    snapshot: StateSnapshot | None = None,
    run_id: UUID | None = None,
    error: BaseException | None = None,
) -> AgentResult:
    """把一次 invoke 的原始输出 / 异常转成统一 AgentResult 信封。"""
    pending = None
    if error is None:
        pending = extract_pending_interaction(output, snapshot, allow_empty=True)

    status = _coerce_status(
        pending=pending, output=output, snapshot=snapshot, error=error
    )
    refs: list[ResultReference] = []
    if error is None:
        try:
            refs = extract_result_refs(output, snapshot)
        except AgentError:
            refs = []

    resolved_run_id = (
        _coerce_run_id(output, snapshot, run_id) if error is None or run_id else None
    )
    if resolved_run_id is None:
        # 失败且无 run_id 时仍需填满信封字段
        resolved_run_id = run_id or uuid4()

    artifacts: dict[str, Any]
    if error is None:
        artifacts = _artifacts_of(output, snapshot)
    else:
        # 失败时把异常细节放进 artifacts，避免只剩 AGENT_ERROR 无法排查
        artifacts = {
            "error_type": type(error).__name__,
            "error_message": str(error),
        }
        cause = error.__cause__ or error.__context__
        if cause is not None:
            artifacts["error_cause_type"] = type(cause).__name__
            artifacts["error_cause_message"] = str(cause)

    return AgentResult(
        status=status,
        thread_id=thread_id,
        run_id=resolved_run_id,
        pending=pending,
        result_refs=refs,
        phase=_phase_of(output, snapshot),
        error_code=_error_code_of(output, snapshot, error),
        artifacts=artifacts,
    )


def normalize_resume_value(resume_command: ResumeCommand | Mapping[str, Any]) -> dict[str, Any]:
    """将 ResumeCommand 或兼容 mapping 规范为 Command(resume=...) 可消费的 dict。"""
    if isinstance(resume_command, ResumeCommand):
        return resume_command.model_dump(mode="json")
    if isinstance(resume_command, Mapping):
        # 允许调用方只传 payload，或传完整 ResumeCommand 字段
        if {
            "workspace_id",
            "thread_id",
            "interaction_id",
            "input_version",
            "payload",
        }.issubset(resume_command.keys()):
            return ResumeCommand.model_validate(resume_command).model_dump(mode="json")
        return dict(resume_command)
    raise InvalidInput("resume_command 必须是 ResumeCommand 或 JSON 对象")


def invoke_compiled(
    graph: Any,
    *,
    thread_id: str,
    graph_input: Any,
    run_id: UUID | None = None,
) -> AgentResult:
    """对已 compile 的图执行一次 invoke，并转成 AgentResult。"""
    config = thread_config(thread_id)
    try:
        output = graph.invoke(graph_input, config)
    except Exception as error:  # noqa: BLE001 — 统一信封，不吞掉细节
        return to_agent_result(
            thread_id=thread_id,
            run_id=run_id,
            error=error,
        )
    if output is not None and not isinstance(output, dict):
        return to_agent_result(
            thread_id=thread_id,
            run_id=run_id,
            error=AgentError("图返回值不是对象"),
        )
    snapshot = None
    # interrupt 时 output 已含 __interrupt__；仍尽量读 snapshot 补 run_id
    try:
        snapshot = graph.get_state(config)
    except Exception:  # noqa: BLE001
        snapshot = None
    return to_agent_result(
        thread_id=thread_id,
        output=output if isinstance(output, dict) else None,
        snapshot=snapshot,
        run_id=run_id,
    )


def resume_compiled(
    graph: Any,
    *,
    thread_id: str,
    resume_command: ResumeCommand | Mapping[str, Any],
    run_id: UUID | None = None,
) -> AgentResult:
    """用 Command(resume=...) 恢复挂起的图。"""
    value = normalize_resume_value(resume_command)
    return invoke_compiled(
        graph,
        thread_id=thread_id,
        graph_input=Command(resume=value),
        run_id=run_id,
    )


def read_state(
    graph: Any,
    *,
    thread_id: str,
) -> dict[str, Any]:
    """读取 checkpoint 状态值与 pending 交互。"""
    config = thread_config(thread_id)
    snapshot = graph.get_state(config)
    values = dict(snapshot.values) if isinstance(snapshot.values, dict) else {}
    pending = extract_pending_interaction(None, snapshot, allow_empty=True)
    return {
        "thread_id": thread_id,
        "values": values,
        "pending": pending.model_dump(mode="json") if pending is not None else None,
        "next": list(snapshot.next) if snapshot.next else [],
    }


__all__ = [
    "GraphKind",
    "build_analysis_thread_id",
    "build_finalize_thread_id",
    "build_task_init_thread_id",
    "extract_pending_interaction",
    "extract_result_refs",
    "infer_graph_kind",
    "invoke_compiled",
    "normalize_resume_value",
    "read_state",
    "resume_compiled",
    "thread_config",
    "to_agent_result",
]
