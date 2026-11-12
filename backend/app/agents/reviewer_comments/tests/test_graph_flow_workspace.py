"""WorkspaceTaskGraph 编译与 FakeStore 跑到第一个 interrupt。"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from langgraph_agent.agent.workspace_task import split_node
from langgraph_agent.agent.workspace_task.graph import (
    WorkspaceTaskStores,
    build_workspace_task_graph,
)
from langgraph_agent.ports.types import (
    PersistTaskInitResult,
    ReviewInputRecord,
    ReviewPartyRecord,
    WorkspaceRecord,
)
from langgraph_agent.schemas import (
    GraphRunStatus,
    LlmSplitResult,
    PendingInteraction,
    ResumeCommand,
    SplitCandidate,
    WorkspaceMode,
)


class FakeWorkspaceStore:
    """内存假存储：对齐 WorkspaceStore 协议，供图流测试。"""

    def __init__(
        self,
        *,
        workspace: WorkspaceRecord,
        inputs: list[ReviewInputRecord],
        parties: list[ReviewPartyRecord] | None = None,
    ) -> None:
        self.workspace = workspace
        self.inputs = inputs
        self.parties = parties or []
        self.persisted: list[dict[str, Any]] = []

    def get_workspace(self, workspace_id: UUID) -> WorkspaceRecord | None:
        if workspace_id != self.workspace["workspace_id"]:
            return None
        return self.workspace

    def list_current_review_inputs(
        self, workspace_id: UUID
    ) -> list[ReviewInputRecord]:
        if workspace_id != self.workspace["workspace_id"]:
            return []
        return list(self.inputs)

    def list_parties(self, workspace_id: UUID) -> list[ReviewPartyRecord]:
        if workspace_id != self.workspace["workspace_id"]:
            return []
        return list(self.parties)

    def persist_task_init_result(
        self,
        *,
        workspace_id: UUID,
        input_version: str,
        confirmed_suggestions: list[dict[str, Any]],
    ) -> PersistTaskInitResult:
        self.persisted.append(
            {
                "workspace_id": workspace_id,
                "input_version": input_version,
                "confirmed_suggestions": confirmed_suggestions,
            }
        )
        refs = []
        for index, _item in enumerate(confirmed_suggestions, start=1):
            refs.append({"type": "suggestion", "id": str(uuid4())})
        return {
            "result_refs": refs,
            "workspace_status": "ACTIVE",
        }


def _fixture() -> tuple[FakeWorkspaceStore, dict[str, Any]]:
    workspace_id = uuid4()
    user_id = f"user-{uuid4().hex[:8]}"
    party_id = uuid4()
    input_id = uuid4()
    workspace: WorkspaceRecord = {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "title": "图流测试",
        "mode": "FAST",
        "status": "ACTIVE",
        "global_settings": {},
        "schema_version": 1,
    }
    review_input: ReviewInputRecord = {
        "review_input_id": input_id,
        "workspace_id": workspace_id,
        "party_id": party_id,
        "version_no": 1,
        "raw_text": (
            "1. Please clarify the sampling procedure.\n"
            "2. Please report confidence intervals for the main results."
        ),
        "storage_uri": None,
        "content_hash": "abc",
        "language": "en",
        "is_current": True,
        "role": "REVIEWER",
        "display_name": "Reviewer #1",
        "raw_label": "Reviewer #1",
    }
    party: ReviewPartyRecord = {
        "party_id": party_id,
        "workspace_id": workspace_id,
        "role": "REVIEWER",
        "display_name": "Reviewer #1",
        "raw_label": "Reviewer #1",
    }
    store = FakeWorkspaceStore(
        workspace=workspace, inputs=[review_input], parties=[party]
    )
    run_id = uuid4()
    thread_id = f"workspace:{workspace_id}:task:{run_id}"
    initial_state = {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "mode": WorkspaceMode.FAST,
        "manuscript_version_id": None,
        "thread_id": thread_id,
        "run_id": run_id,
        "run_scope": "TASK_INIT",
        "input_version": f"test-{run_id.hex}",
        "phase": "PENDING",
        "pending_interaction_id": None,
        "draft_refs": {},
        "result_refs": [],
        "status": GraphRunStatus.RUNNING,
        "error_code": None,
    }
    return store, initial_state


def _fake_split(purpose, schema, messages, *, timeout_seconds=None):
    # 从 human 消息提取「原始审稿意见」原文，quote 必须是其子串。
    human = ""
    for item in messages:
        if isinstance(item, tuple) and item[0] == "human":
            human = str(item[1])
            break
    marker = "原始审稿意见：\n"
    original = human.split(marker, 1)[-1].strip() if marker in human else human.strip()
    quote = original[: min(len(original), 120)] or "Please"
    return LlmSplitResult(
        review_points=[
            SplitCandidate(
                atomic_concern=f"concern:{quote[:40]}",
                explicit_request=quote,
                implicit_concern=None,
                source_quote=quote,
                split_confidence=0.9,
            )
        ]
    )


def test_graph_compiles_with_eleven_business_nodes() -> None:
    fake = FakeWorkspaceStore(
        workspace={
            "workspace_id": uuid4(),
            "user_id": "u",
            "title": "t",
            "mode": "FAST",
            "status": "ACTIVE",
            "global_settings": {},
            "schema_version": 1,
        },
        inputs=[],
    )
    graph = build_workspace_task_graph(
        stores=WorkspaceTaskStores(workspace=fake),
        checkpointer=InMemorySaver(),
    )
    # START、END + 11 业务节点（FAST 7 + SLOW 4）
    assert len(graph.get_graph().nodes) == 13


def test_graph_reaches_first_interrupt(monkeypatch) -> None:
    monkeypatch.setattr(split_node, "invoke_structured", _fake_split)
    store, initial_state = _fixture()
    checkpointer = InMemorySaver()
    graph = build_workspace_task_graph(
        stores=WorkspaceTaskStores(workspace=store),
        checkpointer=checkpointer,
        split_max_workers=1,
    )
    config = {"configurable": {"thread_id": initial_state["thread_id"]}}
    first = graph.invoke(initial_state, config)

    interrupts = first.get("__interrupt__", ())
    assert len(interrupts) == 1
    interaction = PendingInteraction.model_validate(interrupts[0].value)
    assert interaction.interaction_type == "CONFIRM_SUGGESTIONS"
    assert first["phase"] == "CONFIRM_SUGGESTIONS"
    assert first["status"] == GraphRunStatus.WAITING_USER
    assert str(first["pending_interaction_id"]) == str(interaction.interaction_id)
    assert interaction.context["suggestions"]
    assert store.persisted == []


def test_graph_resume_to_relations_and_persist(monkeypatch) -> None:
    monkeypatch.setattr(split_node, "invoke_structured", _fake_split)
    store, initial_state = _fixture()
    checkpointer = InMemorySaver()
    graph = build_workspace_task_graph(
        stores=WorkspaceTaskStores(workspace=store),
        checkpointer=checkpointer,
        split_max_workers=1,
    )
    config = {"configurable": {"thread_id": initial_state["thread_id"]}}
    first = graph.invoke(initial_state, config)
    suggestions_interaction = PendingInteraction.model_validate(
        first["__interrupt__"][0].value
    )

    resume_cmd = ResumeCommand(
        workspace_id=suggestions_interaction.workspace_id,
        thread_id=suggestions_interaction.thread_id,
        interaction_id=suggestions_interaction.interaction_id,
        input_version=suggestions_interaction.input_version,
        payload={"approved": True},
    )
    second = graph.invoke(
        Command(resume=resume_cmd.model_dump(mode="json")), config
    )
    relations_interaction = PendingInteraction.model_validate(
        second["__interrupt__"][0].value
    )
    assert relations_interaction.interaction_type == "CONFIRM_RELATIONS"
    assert second["phase"] == "CONFIRM_RELATIONS"
    assert store.persisted == []

    resume_rel = ResumeCommand(
        workspace_id=relations_interaction.workspace_id,
        thread_id=relations_interaction.thread_id,
        interaction_id=relations_interaction.interaction_id,
        input_version=relations_interaction.input_version,
        payload={"approved": True},
    )
    final = graph.invoke(Command(resume=resume_rel.model_dump(mode="json")), config)
    assert "__interrupt__" not in final
    assert final["phase"] == "READY"
    assert final["status"] == GraphRunStatus.SUCCEEDED
    assert final["result_refs"]
    assert final["draft_refs"] == {}
    assert len(store.persisted) == 1
