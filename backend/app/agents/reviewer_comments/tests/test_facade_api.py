"""C1 验收：ReviewAgent + MemorySaver 的 start → WAITING_HUMAN → resume 状态机。"""

from __future__ import annotations

from typing import Any, TypedDict
from uuid import UUID, uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from langgraph_agent import AgentResult, AgentStatus, ReviewAgent, ResumeCommand
from langgraph_agent.agent.runtime import GraphKind, build_task_init_thread_id
from langgraph_agent.memory import make_memory_checkpointer
from langgraph_agent.schemas.interaction import (
    EditableField,
    EditableFieldType,
    InteractionOption,
    PendingInteraction,
)
from langgraph_agent.schemas.public_api import TaskInitInput
from langgraph_agent.schemas.run import GraphRunStatus
from langgraph_agent.schemas.workspace import WorkspaceMode


# ---------------------------------------------------------------------------
# Fake stores（门面不真正访问端口；mock 图也不需要真实 store 方法）
# ---------------------------------------------------------------------------


class FakeStores(dict):
    """最小 FakeStores：dict 形态，可挂任意键。"""

    def __init__(self) -> None:
        super().__init__()
        self["workspace"] = object()
        self["analysis"] = object()
        self["reply"] = object()
        self["finalize"] = object()


# ---------------------------------------------------------------------------
# Mock 图：两步 interrupt，验证门面状态机形状
# ---------------------------------------------------------------------------


class _MockState(TypedDict):
    workspace_id: UUID
    user_id: str
    mode: WorkspaceMode
    manuscript_version_id: UUID | None
    thread_id: str
    run_id: UUID
    run_scope: str
    input_version: str
    phase: str
    pending_interaction_id: UUID | None
    draft_refs: dict[str, Any]
    result_refs: list[dict[str, str]]
    status: GraphRunStatus
    error_code: str | None
    step: int


def _mock_interaction(state: _MockState, step: int) -> PendingInteraction:
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
        context={"step": step},
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


def _node_wait_first(state: _MockState) -> dict[str, Any]:
    interaction = _mock_interaction(state, 1)
    resume_value = interrupt(interaction.model_dump(mode="json"))
    # resume 后继续
    assert isinstance(resume_value, dict)
    return {
        "phase": "AFTER_FIRST",
        "pending_interaction_id": None,
        "status": GraphRunStatus.RUNNING,
        "step": 2,
        "draft_refs": {"first_resume": resume_value},
    }


def _node_wait_second(state: _MockState) -> dict[str, Any]:
    interaction = _mock_interaction(state, 2)
    resume_value = interrupt(interaction.model_dump(mode="json"))
    assert isinstance(resume_value, dict)
    result_id = uuid4()
    return {
        "phase": "READY",
        "pending_interaction_id": None,
        "status": GraphRunStatus.SUCCEEDED,
        "step": 3,
        "result_refs": [{"type": "suggestion", "id": str(result_id)}],
        "draft_refs": {},
    }


def build_mock_task_graph(*, checkpointer, stores=None):  # noqa: ARG001
    """可注入的 mock TASK_INIT 图：两次 WAITING_HUMAN 后 SUCCEEDED。"""
    graph = StateGraph(_MockState)
    graph.add_node("wait_first", _node_wait_first)
    graph.add_node("wait_second", _node_wait_second)
    graph.add_edge(START, "wait_first")
    graph.add_edge("wait_first", "wait_second")
    graph.add_edge("wait_second", END)
    return graph.compile(
        checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
        name="mock_task_init",
    )


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


def test_public_import() -> None:
    from langgraph_agent import AgentResult as AR
    from langgraph_agent import ReviewAgent as RA

    assert RA is ReviewAgent
    assert AR is AgentResult


def test_make_memory_checkpointer_is_in_memory_saver() -> None:
    cp = make_memory_checkpointer()
    assert isinstance(cp, InMemorySaver)


def test_start_waiting_human_resume_state_machine() -> None:
    """start → WAITING_HUMAN → resume → WAITING_HUMAN → resume → SUCCEEDED。"""
    stores = FakeStores()
    agent = ReviewAgent(
        stores=stores,
        checkpointer=make_memory_checkpointer(),
        graph_builders={GraphKind.TASK_INIT: build_mock_task_graph},
    )

    workspace_id = uuid4()
    user_id = "user-test"
    result1 = agent.start_task_init(
        TaskInitInput(
            workspace_id=workspace_id,
            user_id=user_id,
            mode=WorkspaceMode.FAST,
        )
    )

    assert isinstance(result1, AgentResult)
    assert result1.status is AgentStatus.WAITING_HUMAN
    assert result1.pending is not None
    assert result1.pending.interaction_type == "CONFIRM_SUGGESTIONS"
    assert result1.thread_id
    assert result1.run_id
    thread_id = result1.thread_id
    run_id = result1.run_id

    # get_state 应能读到 pending
    state_view = agent.get_state(thread_id, graph_kind=GraphKind.TASK_INIT)
    assert state_view["pending"] is not None
    assert state_view["pending"]["interaction_type"] == "CONFIRM_SUGGESTIONS"

    resume1 = ResumeCommand(
        workspace_id=result1.pending.workspace_id,
        thread_id=result1.pending.thread_id,
        interaction_id=result1.pending.interaction_id,
        input_version=result1.pending.input_version,
        payload={"approved": True},
    )
    result2 = agent.resume(thread_id, resume1)

    assert result2.status is AgentStatus.WAITING_HUMAN
    assert result2.pending is not None
    assert result2.pending.interaction_type == "CONFIRM_RELATIONS"
    assert result2.thread_id == thread_id
    assert result2.run_id == run_id

    resume2 = ResumeCommand(
        workspace_id=result2.pending.workspace_id,
        thread_id=result2.pending.thread_id,
        interaction_id=result2.pending.interaction_id,
        input_version=result2.pending.input_version,
        payload={"approved": True},
    )
    result3 = agent.resume(thread_id, resume2)

    assert result3.status is AgentStatus.SUCCEEDED
    assert result3.pending is None
    assert result3.thread_id == thread_id
    assert result3.run_id == run_id
    assert result3.phase == "READY"
    assert len(result3.result_refs) == 1
    assert result3.result_refs[0].type == "suggestion"


def test_resume_infers_graph_kind_from_thread_id() -> None:
    stores = FakeStores()
    agent = ReviewAgent.from_memory(
        stores,
        graph_builders={GraphKind.TASK_INIT: build_mock_task_graph},
    )
    workspace_id = uuid4()
    first = agent.start_task_init(
        {
            "workspace_id": workspace_id,
            "user_id": "u1",
            "mode": "FAST",
        }
    )
    assert first.status is AgentStatus.WAITING_HUMAN
    assert first.pending is not None

    # 清空内部登记，强制走 thread_id 推断
    agent._thread_kinds.clear()
    cmd = ResumeCommand(
        workspace_id=first.pending.workspace_id,
        thread_id=first.pending.thread_id,
        interaction_id=first.pending.interaction_id,
        input_version=first.pending.input_version,
        payload={"approved": True},
    )
    second = agent.resume(first.thread_id, cmd)
    assert second.status is AgentStatus.WAITING_HUMAN
    assert second.pending is not None
    assert second.pending.interaction_type == "CONFIRM_RELATIONS"


def test_thread_id_builder_matches_backend_convention() -> None:
    workspace_id = uuid4()
    run_id = uuid4()
    assert build_task_init_thread_id(workspace_id, run_id) == (
        f"workspace:{workspace_id}:task:{run_id}"
    )


def test_failed_graph_maps_to_agent_failed() -> None:
    def boom_builder(*, checkpointer, stores=None):  # noqa: ARG001
        def _boom(_state: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("故意失败")

        class S(TypedDict):
            workspace_id: UUID
            user_id: str
            mode: WorkspaceMode
            manuscript_version_id: UUID | None
            thread_id: str
            run_id: UUID
            run_scope: str
            input_version: str
            phase: str
            pending_interaction_id: UUID | None
            draft_refs: dict[str, Any]
            result_refs: list[dict[str, str]]
            status: GraphRunStatus
            error_code: str | None

        g = StateGraph(S)
        g.add_node("boom", _boom)
        g.add_edge(START, "boom")
        g.add_edge("boom", END)
        return g.compile(checkpointer=checkpointer)

    agent = ReviewAgent(
        stores=FakeStores(),
        checkpointer=make_memory_checkpointer(),
        graph_builders={GraphKind.TASK_INIT: boom_builder},
    )
    result = agent.start_task_init(
        TaskInitInput(workspace_id=uuid4(), user_id="u", mode=WorkspaceMode.FAST)
    )
    assert result.status is AgentStatus.FAILED
    assert result.error_code == "AGENT_ERROR"
