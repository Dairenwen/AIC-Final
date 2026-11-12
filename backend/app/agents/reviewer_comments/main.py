"""langgraph-agent 最小 CLI（2B：SDK + CLI，不启动 HTTP）。

子命令
------
- demo-offline   : Memory + FakeStores + mock 图干跑，无需密钥，退出码 0
- demo-task-init : 读取示例 JSON，调用 start_task_init，打印 AgentResult
- resume         : 读取 resume payload，调用 resume

人工确认
--------
状态为 WAITING_HUMAN 时打印 pending 与 thread_id。
传入 ``--auto-approve`` 时用固定 ResumeCommand 自动继续。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TypedDict
from uuid import UUID, uuid4

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 路径与环境
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent
_EXAMPLES = _ROOT / "assets" / "examples"
_DEFAULT_TASK_INIT = _EXAMPLES / "sample_task_init.json"
_DEFAULT_RESUME = _EXAMPLES / "sample_resume.json"

# 保证未 editable install 时也能从包根直接运行
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_env() -> None:
    """加载 langgraph-agent/.env（若存在）；不覆盖已有环境变量。"""
    load_dotenv(_ROOT / ".env", override=False)
    # 兼容从仓库根启动：再尝试 cwd
    load_dotenv(override=False)


# ---------------------------------------------------------------------------
# 输出辅助
# ---------------------------------------------------------------------------


def _dump(obj: Any) -> str:
    if hasattr(obj, "model_dump"):
        data = obj.model_dump(mode="json")
    else:
        data = obj
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _print_result(result: Any, *, label: str | None = None) -> None:
    if label:
        print(f"=== {label} ===")
    print(_dump(result))
    status = getattr(result, "status", None)
    status_value = status.value if hasattr(status, "value") else status
    if status_value == "WAITING_HUMAN":
        pending = getattr(result, "pending", None)
        print("\n--- WAITING_HUMAN ---")
        print(f"thread_id: {getattr(result, 'thread_id', '')}")
        if pending is not None:
            print("pending:")
            print(_dump(pending))
        else:
            print("pending: null")


def _exit_for(result: Any) -> int:
    status = getattr(result, "status", None)
    value = status.value if hasattr(status, "value") else str(status)
    if value == "FAILED":
        return 1
    return 0


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"找不到 JSON 文件：{path}")
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"JSON 根节点必须是对象：{path}")
    return data


# ---------------------------------------------------------------------------
# Offline mock（demo-offline / 无密钥演示；不改 graph 业务文件）
# ---------------------------------------------------------------------------


class _FakeStores(dict):
    """最小 FakeStores：dict 形态，含 finalize 占位。"""

    def __init__(self) -> None:
        super().__init__()
        self["workspace"] = object()
        self["manuscript"] = object()
        self["analysis"] = object()
        self["reply"] = object()
        self["finalize"] = object()
        self["run"] = object()


class _MockState(TypedDict):
    workspace_id: UUID
    user_id: str
    mode: Any
    manuscript_version_id: UUID | None
    thread_id: str
    run_id: UUID
    run_scope: str
    input_version: str
    phase: str
    pending_interaction_id: UUID | None
    draft_refs: dict[str, Any]
    result_refs: list[dict[str, str]]
    status: Any
    error_code: str | None
    step: int


def _build_mock_task_graph(*, checkpointer: Any, stores: Any = None):  # noqa: ARG001
    """可注入的 mock TASK_INIT 图：两次 WAITING_HUMAN 后 SUCCEEDED。"""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    from langgraph_agent.schemas.interaction import (
        EditableField,
        EditableFieldType,
        InteractionOption,
        PendingInteraction,
    )
    from langgraph_agent.schemas.run import GraphRunStatus

    def _interaction(state: _MockState, step: int) -> PendingInteraction:
        interaction_type = (
            "CONFIRM_SUGGESTIONS" if step == 1 else "CONFIRM_RELATIONS"
        )
        return PendingInteraction(
            interaction_id=uuid4(),
            interaction_type=interaction_type,
            workspace_id=UUID(str(state["workspace_id"])),
            suggestion_id=None,
            source_id=None,
            thread_id=str(state["thread_id"]),
            input_version=str(state["input_version"]),
            title=f"确认步骤 {step}",
            question=f"请确认第 {step} 步。",
            context={"step": step, "demo": True},
            options=[InteractionOption(value="approve", label="确认")],
            editable_fields=[
                EditableField(
                    key="approved",
                    label="是否确认",
                    type=EditableFieldType.CHECKBOX,
                    required=True,
                    default=True,
                )
            ],
            blockers=[],
            resume_action="confirm",
        )

    def wait_first(state: _MockState) -> dict[str, Any]:
        interaction = _interaction(state, 1)
        resume_value = interrupt(interaction.model_dump(mode="json"))
        return {
            "phase": "AFTER_FIRST",
            "pending_interaction_id": None,
            "status": GraphRunStatus.RUNNING,
            "step": 2,
            "draft_refs": {"first_resume": resume_value if isinstance(resume_value, dict) else {}},
        }

    def wait_second(state: _MockState) -> dict[str, Any]:
        interaction = _interaction(state, 2)
        resume_value = interrupt(interaction.model_dump(mode="json"))
        result_id = uuid4()
        return {
            "phase": "READY",
            "pending_interaction_id": None,
            "status": GraphRunStatus.SUCCEEDED,
            "step": 3,
            "result_refs": [{"type": "suggestion", "id": str(result_id)}],
            "draft_refs": {
                "second_resume": resume_value if isinstance(resume_value, dict) else {}
            },
        }

    graph = StateGraph(_MockState)
    graph.add_node("wait_first", wait_first)
    graph.add_node("wait_second", wait_second)
    graph.add_edge(START, "wait_first")
    graph.add_edge("wait_first", "wait_second")
    graph.add_edge("wait_second", END)
    return graph.compile(
        checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
        name="cli_mock_task_init",
    )


def _build_offline_agent():
    """Memory + FakeStores + mock TASK_INIT 图。"""
    from langgraph_agent import ReviewAgent
    from langgraph_agent.agent.runtime import GraphKind

    return ReviewAgent.from_memory(
        _FakeStores(),
        graph_builders={GraphKind.TASK_INIT: _build_mock_task_graph},
    )


def _build_live_agent():
    """按 .env / Settings 装配 Postgres stores + checkpointer。"""
    from langgraph_agent import ReviewAgent

    return ReviewAgent.from_settings()


def _build_auto_resume_command(pending: Any):
    """根据 pending 构造演示用 ResumeCommand（默认批准）。"""
    from langgraph_agent import ResumeCommand
    from langgraph_agent.schemas.interaction import EditableFieldType

    payload: dict[str, Any] = {}
    for field in getattr(pending, "editable_fields", []) or []:
        if field.default is not None:
            payload[field.key] = field.default
        elif field.type is EditableFieldType.CHECKBOX:
            payload[field.key] = True
        elif field.key == "approved":
            payload[field.key] = True
        else:
            payload[field.key] = field.default
    if not payload:
        payload = {"approved": True}
    return ResumeCommand(
        workspace_id=pending.workspace_id,
        thread_id=pending.thread_id,
        interaction_id=pending.interaction_id,
        input_version=pending.input_version,
        payload=payload,
    )


def _auto_approve_loop(agent: Any, result: Any, *, max_steps: int = 8) -> Any:
    """WAITING_HUMAN 时自动 resume，直到结束或达到步数上限。"""
    from langgraph_agent import AgentStatus

    step = 0
    current = result
    while current.status is AgentStatus.WAITING_HUMAN and step < max_steps:
        if current.pending is None:
            print("WAITING_HUMAN 但 pending 为空，停止 auto-approve。", file=sys.stderr)
            break
        step += 1
        cmd = _build_auto_resume_command(current.pending)
        print(f"\n[auto-approve] 第 {step} 步 resume → {current.pending.interaction_type}")
        current = agent.resume(current.thread_id, cmd)
        _print_result(current, label=f"resume#{step}")
    return current


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------


def cmd_demo_offline(args: argparse.Namespace) -> int:
    """无密钥干跑：start →（可选 auto-approve）→ 打印结果。"""
    from langgraph_agent import AgentStatus, TaskInitInput
    from langgraph_agent.schemas.workspace import WorkspaceMode

    print("demo-offline：Memory + FakeStores + mock 图（无需 LLM / DB）")
    agent = _build_offline_agent()

    payload = {
        "workspace_id": "00000000-0000-4000-8000-000000000001",
        "user_id": "demo-offline-user",
        "mode": WorkspaceMode.FAST.value,
    }
    if args.input:
        payload.update(_load_json(Path(args.input)))

    result = agent.start_task_init(TaskInitInput.model_validate(payload))
    _print_result(result, label="start_task_init")

    auto = True if args.auto_approve is None else bool(args.auto_approve)
    if auto and result.status is AgentStatus.WAITING_HUMAN:
        result = _auto_approve_loop(agent, result)

    code = _exit_for(result)
    print(f"\ndemo-offline 完成，status={result.status.value}，exit={code}")
    return code


def cmd_demo_task_init(args: argparse.Namespace) -> int:
    """读取示例，调用 start_task_init，打印 AgentResult JSON。"""
    from langgraph_agent import AgentStatus, TaskInitInput

    input_path = Path(args.input) if args.input else _DEFAULT_TASK_INIT
    raw = _load_json(input_path)
    task_input = TaskInitInput.model_validate(raw)

    if args.live:
        print("demo-task-init：使用 ReviewAgent.from_settings()（需要 .env）")
        agent = _build_live_agent()
    else:
        print(
            "demo-task-init：离线 mock 图（默认）。"
            "真配置请加 --live（需 DATABASE_URL / LLM）。"
        )
        agent = _build_offline_agent()

    result = agent.start_task_init(task_input)
    _print_result(result, label="start_task_init")

    if result.status is AgentStatus.FAILED:
        print(
            "\n[失败详情] 请查看上方 JSON 的 artifacts.error_type / error_message；"
            "常见原因：1) 未 seed 工作区 2) LLM 超时（默认 30s 对慢模型不够，"
            "请把 .env 中 LLM_TIMEOUT_SECONDS 调到 180~300）"
            " 3) DATABASE_URL 连不上。",
            file=sys.stderr,
        )

    if args.auto_approve and result.status is AgentStatus.WAITING_HUMAN:
        result = _auto_approve_loop(agent, result)

    return _exit_for(result)


def cmd_resume(args: argparse.Namespace) -> int:
    """读取 resume payload，调用 resume。"""
    from langgraph_agent import AgentStatus, ResumeCommand
    from langgraph_agent.agent.runtime import GraphKind

    input_path = Path(args.input) if args.input else _DEFAULT_RESUME
    raw = _load_json(input_path)

    # 允许文件内带 thread_id，或由 CLI --thread-id 覆盖
    thread_id = args.thread_id or raw.get("thread_id")
    if not thread_id:
        print("错误：resume 需要 thread_id（文件字段或 --thread-id）", file=sys.stderr)
        return 2

    # graph_kind 可选
    graph_kind = None
    if args.graph_kind:
        graph_kind = GraphKind(args.graph_kind)

    if args.live:
        print("resume：使用 ReviewAgent.from_settings()")
        agent = _build_live_agent()
    else:
        print(
            "resume：离线 mock 模式。"
            "注意：MemorySaver 是进程内状态，单独调用 resume 通常无法接上前一次 offline 运行；"
            "完整演示请用 demo-offline / demo-task-init --auto-approve。"
            "真配置续跑请加 --live。"
        )
        agent = _build_offline_agent()

    # 若仅有 payload 且缺少 ResumeCommand 必备字段，尝试从 pending 补（需 get_state）
    required = {"workspace_id", "thread_id", "interaction_id", "input_version", "payload"}
    if not required.issubset(raw.keys()):
        if "payload" not in raw:
            print("错误：resume JSON 至少需要 payload 字段", file=sys.stderr)
            return 2
        try:
            state = agent.get_state(str(thread_id), graph_kind=graph_kind)
        except Exception as exc:  # noqa: BLE001
            print(f"错误：无法 get_state 补全 ResumeCommand：{exc}", file=sys.stderr)
            return 1
        pending = state.get("pending")
        if not pending:
            print("错误：checkpoint 无 pending，无法自动补全 ResumeCommand", file=sys.stderr)
            return 1
        cmd = ResumeCommand(
            workspace_id=pending["workspace_id"],
            thread_id=pending["thread_id"],
            interaction_id=pending["interaction_id"],
            input_version=pending["input_version"],
            payload=raw["payload"],
        )
    else:
        raw = {**raw, "thread_id": thread_id}
        cmd = ResumeCommand.model_validate(raw)

    result = agent.resume(str(thread_id), cmd, graph_kind=graph_kind)
    _print_result(result, label="resume")

    if args.auto_approve and result.status is AgentStatus.WAITING_HUMAN:
        result = _auto_approve_loop(agent, result)

    return _exit_for(result)


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="langgraph-agent 最小 CLI（SDK 演示，不启动 HTTP）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # demo-offline
    p_off = sub.add_parser(
        "demo-offline",
        help="无密钥干跑：Memory + FakeStores + mock 图，默认 auto-approve 到结束",
    )
    p_off.add_argument(
        "--input",
        type=str,
        default=None,
        help="可选：覆盖 TaskInitInput 的 JSON 路径",
    )
    p_off.add_argument(
        "--auto-approve",
        dest="auto_approve",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="WAITING_HUMAN 时自动确认（默认开启；--no-auto-approve 关闭）",
    )
    p_off.set_defaults(func=cmd_demo_offline)

    # demo-task-init
    p_init = sub.add_parser(
        "demo-task-init",
        help="读取 sample_task_init.json，调用 start_task_init，打印 AgentResult",
    )
    p_init.add_argument(
        "--input",
        type=str,
        default=None,
        help=f"TaskInitInput JSON（默认 {_DEFAULT_TASK_INIT.name}）",
    )
    p_init.add_argument(
        "--live",
        action="store_true",
        help="使用 ReviewAgent.from_settings()（需 .env 中 DATABASE_URL 等）",
    )
    p_init.add_argument(
        "--auto-approve",
        action="store_true",
        help="WAITING_HUMAN 时自动确认继续",
    )
    p_init.set_defaults(func=cmd_demo_task_init)

    # resume
    p_res = sub.add_parser(
        "resume",
        help="读取 resume payload，调用 ReviewAgent.resume",
    )
    p_res.add_argument(
        "--input",
        type=str,
        default=None,
        help=f"ResumeCommand JSON（默认 {_DEFAULT_RESUME.name}）",
    )
    p_res.add_argument(
        "--thread-id",
        type=str,
        default=None,
        help="覆盖 JSON 中的 thread_id",
    )
    p_res.add_argument(
        "--graph-kind",
        type=str,
        choices=["TASK_INIT", "ANALYSIS", "REPLY", "FINALIZE"],
        default=None,
        help="可选：显式指定图类型",
    )
    p_res.add_argument(
        "--live",
        action="store_true",
        help="使用 ReviewAgent.from_settings() 续跑（Postgres checkpointer）",
    )
    p_res.add_argument(
        "--auto-approve",
        action="store_true",
        help="若仍 WAITING_HUMAN 则继续自动确认",
    )
    p_res.set_defaults(func=cmd_resume)

    return parser


def main(argv: list[str] | None = None) -> int:
    _load_env()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 — CLI 边界统一错误输出
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
