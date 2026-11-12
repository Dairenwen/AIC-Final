"""WorkspaceTaskGraph 慢速模式：论文理解基线子链节点。

读写作仅经 ManuscriptStore 端口；确认逻辑为纯函数。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid5

from langgraph.types import interrupt

from langgraph_agent.agent.state import WorkspaceTaskState
from langgraph_agent.ports.manuscript_store import ManuscriptStore
from langgraph_agent.ports.types import PaperCardRecord
from langgraph_agent.schemas import (
    EditableField,
    EditableFieldType,
    GraphRunStatus,
    InteractionOption,
    PendingInteraction,
    ResumeCommand,
)
from langgraph_agent.tools.paper_schemas import CardType, ConfirmationStatus


def _draft(state: WorkspaceTaskState) -> dict[str, Any]:
    return dict(state.get("draft_refs", {}))


def _thread_id(state: WorkspaceTaskState) -> str:
    return state["thread_id"]


def _interaction_id(state: WorkspaceTaskState, interaction_type: str) -> UUID:
    return uuid5(UUID(str(state["run_id"])), interaction_type)


def _resume_payload(
    value: object,
    interaction: PendingInteraction,
) -> dict[str, Any]:
    """与 confirm_suggestions 相同的 ResumeCommand 四项校验。"""
    if not isinstance(value, dict):
        raise ValueError("恢复数据必须是 JSON 对象")
    command_keys = {
        "workspace_id",
        "thread_id",
        "interaction_id",
        "input_version",
        "payload",
    }
    if command_keys.issubset(value):
        command = ResumeCommand.model_validate(value)
        if command.workspace_id != interaction.workspace_id:
            raise ValueError("恢复命令的 workspace_id 不匹配")
        if command.thread_id != interaction.thread_id:
            raise ValueError("恢复命令的 thread_id 不匹配")
        if command.interaction_id != interaction.interaction_id:
            raise ValueError("恢复命令的 interaction_id 不匹配")
        if command.input_version != interaction.input_version:
            raise ValueError("恢复命令的 input_version 已过期")
        return dict(command.payload)
    return dict(value)


def card_record_to_proposal(card: PaperCardRecord | dict[str, Any] | object) -> dict[str, Any]:
    """把 PaperCardRecord / 字典转成 draft_refs 中的提案。"""
    if isinstance(card, dict):
        return {
            "paper_card_id": str(card["paper_card_id"]),
            "card_type": card["card_type"],
            "content": card["content"],
            "source_sections": list(card.get("source_sections") or []),
            "source_quote": card.get("source_quote") or "",
            "confidence": float(card.get("confidence", 0.0)),
            "confirmation_status": card["confirmation_status"],
        }
    return {
        "paper_card_id": str(card.paper_card_id),  # type: ignore[attr-defined]
        "card_type": card.card_type,  # type: ignore[attr-defined]
        "content": card.content,  # type: ignore[attr-defined]
        "source_sections": list(card.source_sections or []),  # type: ignore[attr-defined]
        "source_quote": card.source_quote,  # type: ignore[attr-defined]
        "confidence": float(card.confidence),  # type: ignore[attr-defined]
        "confirmation_status": card.confirmation_status,  # type: ignore[attr-defined]
    }


def structure_summary_to_parsed_paper(
    manuscript_version_id: UUID,
    structure_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    """从 structure_summary 组装 draft_refs.parsed_paper。"""
    summary = structure_summary if isinstance(structure_summary, dict) else {}
    sections = summary.get("sections")
    if not isinstance(sections, list):
        sections = []
    warnings = summary.get("parse_warnings")
    if not isinstance(warnings, list):
        warnings = []
    return {
        "manuscript_version_id": str(manuscript_version_id),
        "title": str(summary.get("title") or ""),
        "abstract": str(summary.get("abstract") or ""),
        "sections": sections,
        "parse_warnings": warnings,
    }


def apply_baseline_confirmation(
    proposals: list[dict[str, Any]],
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """根据用户确认结果生成待落库卡片列表（不写库）。

    每项含 action：
    - update: 已有卡确认/编辑/删除
    - create: 用户补充新卡
    """
    if payload.get("approved") is False:
        raise ValueError("论文理解基线未确认，图不能继续")

    by_id = {str(item["paper_card_id"]): dict(item) for item in proposals}

    selected = payload.get("selected_card_ids")
    if selected is None:
        selected_ids = list(by_id)
    elif isinstance(selected, list):
        selected_ids = [str(item) for item in selected]
    else:
        raise ValueError("selected_card_ids 必须是数组")

    for card_id in selected_ids:
        if card_id not in by_id:
            raise ValueError(f"未知基线卡片：{card_id}")

    content_overrides: dict[str, str] = {}
    rejected_ids: set[str] = set()
    edited = payload.get("cards", [])
    if not isinstance(edited, list):
        raise ValueError("cards 必须是数组")
    for item in edited:
        if not isinstance(item, dict):
            raise ValueError("编辑后的卡片必须是 JSON 对象")
        card_id = str(item.get("paper_card_id", ""))
        if card_id not in by_id:
            raise ValueError(f"未知基线卡片：{card_id}")
        if item.get("accepted") is False:
            rejected_ids.add(card_id)
            selected_ids = [value for value in selected_ids if value != card_id]
        if "content" in item:
            content = str(item["content"]).strip()
            if not content:
                raise ValueError("卡片 content 不能为空")
            content_overrides[card_id] = content

    confirmed: list[dict[str, Any]] = []
    selected_set = set(selected_ids) - rejected_ids
    for card_id, proposal in by_id.items():
        entry = dict(proposal)
        entry["action"] = "update"
        if card_id not in selected_set:
            entry["confirmation_status"] = ConfirmationStatus.DELETED.value
            confirmed.append(entry)
            continue
        new_content = content_overrides.get(card_id)
        if new_content is not None and new_content != proposal["content"]:
            entry["content"] = new_content
            entry["confirmation_status"] = ConfirmationStatus.EDITED.value
        else:
            entry["confirmation_status"] = ConfirmationStatus.CONFIRMED.value
        confirmed.append(entry)

    new_cards = payload.get("new_cards", [])
    if not isinstance(new_cards, list):
        raise ValueError("new_cards 必须是数组")
    valid_types = {item.value for item in CardType}
    for item in new_cards:
        if not isinstance(item, dict):
            raise ValueError("补充卡片必须是 JSON 对象")
        card_type = str(item.get("card_type", "")).strip()
        if card_type not in valid_types:
            raise ValueError(f"非法 card_type：{card_type}")
        content = str(item.get("content", "")).strip()
        if not content:
            raise ValueError("补充卡片 content 不能为空")
        source_sections = item.get("source_sections", [])
        if not isinstance(source_sections, list):
            raise ValueError("source_sections 必须是数组")
        source_quote = str(item.get("source_quote", ""))
        confidence = item.get("confidence", 1.0)
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError) as error:
            raise ValueError("confidence 必须是数字") from error
        confirmed.append(
            {
                "action": "create",
                "paper_card_id": None,
                "card_type": card_type,
                "content": content,
                "source_sections": source_sections,
                "source_quote": source_quote,
                "confidence": confidence_value,
                "confirmation_status": ConfirmationStatus.CONFIRMED.value,
            }
        )
    return confirmed


def parse_manuscript(
    state: WorkspaceTaskState,
    manuscript_store: ManuscriptStore,
) -> dict[str, object]:
    """读取本次任务指定的 ManuscriptVersion，把结构写入 draft_refs。"""
    workspace_id = UUID(str(state["workspace_id"]))
    raw_manuscript_version_id = state.get("manuscript_version_id")
    if raw_manuscript_version_id is None:
        raise ValueError("慢速任务缺少 manuscript_version_id")
    manuscript_version_id = UUID(str(raw_manuscript_version_id))
    manuscript = manuscript_store.get_manuscript_version(manuscript_version_id)
    if manuscript is None:
        raise ValueError("论文版本不存在")
    if manuscript["workspace_id"] != workspace_id:
        raise ValueError("论文版本不属于当前 Workspace")
    if manuscript["parse_status"] == "PENDING":
        raise ValueError("论文仍在解析")
    if manuscript["parse_status"] == "FAILED":
        raise ValueError("论文解析失败")
    if manuscript["parse_status"] != "SUCCEEDED":
        raise ValueError(f"不支持的论文解析状态：{manuscript['parse_status']}")
    parsed_paper = structure_summary_to_parsed_paper(
        manuscript["manuscript_version_id"],
        manuscript.get("structure_summary"),
    )

    draft_refs = _draft(state)
    draft_refs["parsed_paper"] = parsed_paper
    return {
        "phase": "GENERATE_BASELINE_CARDS",
        "draft_refs": draft_refs,
        "status": GraphRunStatus.RUNNING,
    }


def generate_baseline_cards(
    state: WorkspaceTaskState,
    manuscript_store: ManuscriptStore,
) -> dict[str, object]:
    """读取 paper_cards 候选（PENDING），汇总进 draft_refs，不落库。"""
    draft_refs = _draft(state)
    parsed_paper = draft_refs.get("parsed_paper")
    if not isinstance(parsed_paper, dict):
        raise ValueError("parse_manuscript 未提供 parsed_paper")
    manuscript_version_id = UUID(str(parsed_paper["manuscript_version_id"]))
    workspace_id = UUID(str(state["workspace_id"]))

    cards = manuscript_store.get_paper_cards(
        workspace_id,
        manuscript_version_id,
        confirmed_only=False,
    )
    proposals = [
        card_record_to_proposal(card)
        for card in cards
        if card.get("confirmation_status") == ConfirmationStatus.PENDING.value
    ]

    draft_refs["baseline_card_proposals"] = proposals
    draft_refs["baseline_degraded"] = len(proposals) == 0
    interaction_id = _interaction_id(state, "CONFIRM_BASELINE")
    return {
        "phase": "CONFIRM_BASELINE",
        "pending_interaction_id": interaction_id,
        "draft_refs": draft_refs,
        "status": GraphRunStatus.WAITING_USER,
    }


def _baseline_interaction(state: WorkspaceTaskState) -> PendingInteraction:
    draft_refs = _draft(state)
    proposals = draft_refs.get("baseline_card_proposals", [])
    if not isinstance(proposals, list):
        proposals = []
    choices = [
        InteractionOption(
            value=str(item["paper_card_id"]),
            label=str(item.get("content", ""))[:80] or str(item["paper_card_id"]),
            description=str(item.get("card_type", "")),
        )
        for item in proposals
        if isinstance(item, dict) and item.get("paper_card_id")
    ]
    fields: list[EditableField] = []
    if choices:
        fields.append(
            EditableField(
                key="selected_card_ids",
                label="保留的信息卡片",
                type=EditableFieldType.MULTISELECT,
                required=False,
                default=[choice.value for choice in choices],
                choices=choices,
                help_text="取消勾选将标记为删除；可在 payload.cards 中编辑 content。",
            )
        )
    parsed_paper = draft_refs.get("parsed_paper")
    manuscript_version_id = (
        parsed_paper.get("manuscript_version_id")
        if isinstance(parsed_paper, dict)
        else None
    )
    return PendingInteraction(
        interaction_id=UUID(str(state["pending_interaction_id"])),
        interaction_type="CONFIRM_BASELINE",
        workspace_id=UUID(str(state["workspace_id"])),
        suggestion_id=None,
        source_id=None,
        thread_id=_thread_id(state),
        input_version=state["input_version"],
        title="确认论文理解基线",
        question="请确认、编辑或删除信息卡片，也可补充新卡片。",
        context={
            "manuscript_version_id": str(manuscript_version_id or ""),
            "parsed_paper": parsed_paper,
            "cards": proposals,
            "baseline_degraded": bool(draft_refs.get("baseline_degraded")),
        },
        options=choices,
        editable_fields=fields,
        blockers=[],
        resume_action="confirm_baseline",
    )


def confirm_baseline(state: WorkspaceTaskState) -> dict[str, object]:
    """interrupt 挂起；恢复后应用确认结果到 draft_refs，不写库。"""
    interaction = _baseline_interaction(state)
    payload = _resume_payload(
        interrupt(interaction.model_dump(mode="json")), interaction
    )
    draft_refs = _draft(state)
    proposals = draft_refs.get("baseline_card_proposals", [])
    if not isinstance(proposals, list):
        raise ValueError("缺少基线卡片提案")
    draft_refs["confirmed_baseline"] = apply_baseline_confirmation(
        proposals, payload
    )
    return {
        "phase": "PERSIST_BASELINE",
        "pending_interaction_id": None,
        "draft_refs": draft_refs,
        "status": GraphRunStatus.RUNNING,
    }


def persist_baseline(
    state: WorkspaceTaskState,
    manuscript_store: ManuscriptStore,
) -> dict[str, object]:
    """经 ManuscriptStore 把确认后的卡片写回，phase 推进到 PENDING。"""
    draft_refs = _draft(state)
    confirmed = draft_refs.get("confirmed_baseline")
    if not isinstance(confirmed, list):
        raise ValueError("缺少已确认的基线卡片")
    parsed_paper = draft_refs.get("parsed_paper")
    if not isinstance(parsed_paper, dict):
        raise ValueError("缺少 parsed_paper")

    workspace_id = UUID(str(state["workspace_id"]))
    manuscript_version_id = UUID(str(parsed_paper["manuscript_version_id"]))
    manuscript = manuscript_store.get_manuscript_version(manuscript_version_id)
    if manuscript is None:
        raise ValueError("论文版本不存在")
    if manuscript["workspace_id"] != workspace_id:
        raise ValueError("论文版本不属于当前 Workspace")
    if manuscript["parse_status"] != "SUCCEEDED":
        raise ValueError("论文解析状态已变化，无法设置基线")

    manuscript_store.save_baseline_cards(
        workspace_id=workspace_id,
        manuscript_version_id=manuscript_version_id,
        confirmed_cards=confirmed,
    )

    return {
        "phase": "PENDING",
        "pending_interaction_id": None,
        "draft_refs": draft_refs,
        "status": GraphRunStatus.RUNNING,
    }
