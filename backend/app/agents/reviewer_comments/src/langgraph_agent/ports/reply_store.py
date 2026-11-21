"""回复上下文加载与草稿/审核落库端口。"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from langgraph_agent.ports.types import (
    ReplyContext,
    SaveReplyDraftResult,
    SaveReviewDecisionResult,
)


class ReplyStore(Protocol):
    """来源级回复与草稿端口。"""

    def load_reply_context(
        self,
        *,
        workspace_id: UUID,
        suggestion_id: UUID,
        source_id: UUID,
        expression_settings: dict[str, Any] | None = None,
    ) -> ReplyContext:
        """加载回复图所需上下文。

        对应 backend：
        - `WorkspaceRepository.get_by_id`
        - `SuggestionRepository.get_source`
        - `AnalysisRepository.get_current_snapshot` + `list_facts`
        - `get_effective_response_settings`（workspace + source override）
        - `ReplyRepository.list_by_suggestion` + `get_current_draft`
        组合逻辑对齐 `reply_graph.load_context`。
        若传入 `expression_settings` 且与库中生效设置不一致，应报错
        （对齐图内「回复表达设置已变化」校验）。
        """
        ...

    def save_reply_draft(
        self,
        *,
        workspace_id: UUID,
        suggestion_id: UUID,
        source_id: UUID,
        run_id: UUID,
        input_version: str,
        user_id: str,
        strategy: dict[str, Any],
        expression_settings: dict[str, Any],
        response_facts: dict[str, Any] | list[Any],
        generated_draft: dict[str, Any],
        consistency_report: dict[str, Any] | list[Any],
    ) -> SaveReplyDraftResult:
        """写入/更新 SourceReply 与本 run 的 GENERATED 草稿。

        对应 backend：
        - `ReplyRepository.get_by_source_id` / `create_reply`
        - `ReplyRepository.list_drafts` / `create_draft` / `set_current_draft`
        - `ReplyRepository.update_draft_status`（旧版本 STALE）
        - `AuditRepository.create`（策略/事实确认）
        组合逻辑：`graphs/reply_persist.py::persist_and_review`。
        """
        ...

    def save_review_decision(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        user_id: str,
        reply_id: UUID,
        draft_id: UUID,
        decision: dict[str, Any],
    ) -> SaveReviewDecisionResult:
        """应用人工审核决定：approve 批准，或 edit 生成新 EDITED 草稿。

        对应 backend：
        - `ReplyRepository.get_reply` / `get_draft`
        - `ReplyRepository.update_draft_status` / `update_reply_status`
        - `ReplyRepository.create_draft` / `set_current_draft`（edit 路径）
        - `AuditRepository.create`
        - `graphs/reply_sync.propagate_approved_reply_to_siblings`（approve 同步）
        组合逻辑：`graphs/reply_persist.py::persist_review_decision`。
        """
        ...
