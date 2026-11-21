"""分析上下文加载与分析结果落库端口。"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from langgraph_agent.ports.types import (
    AnalysisContext,
    AnalysisSnapshotRecord,
    ModificationFactRecord,
    SaveAnalysisResult,
)


class AnalysisStore(Protocol):
    """建议级分析快照与修改事实端口。"""

    def load_analysis_context(
        self,
        *,
        workspace_id: UUID,
        suggestion_id: UUID,
        input_version: str,
        manuscript_version_id: UUID | None = None,
    ) -> AnalysisContext:
        """加载分析运行所需上下文。

        对应 backend：
        - `SuggestionRepository.get_suggestion` + `list_sources`
        - `AnalysisRepository.get_current_snapshot`
        - `AnalysisRepository.list_facts`（CONFIRMED）
        - `ManuscriptRepository.get_by_id` + `PaperCardRepository.list_confirmed`
          （对齐 `analysis_graph._load_paper_baseline`）
        - 复用判定对齐 `analysis_graph.check_reuse`
        """
        ...

    def save_analysis_snapshot(
        self,
        *,
        suggestion_id: UUID,
        workspace_id: UUID,
        run_id: UUID,
        input_version: str,
        user_id: str,
        classification: dict[str, Any],
        evidence: dict[str, Any],
        priority: dict[str, Any],
        recommended_actions: dict[str, Any] | list[dict[str, Any]],
        classification_confirmed_by_user: bool = False,
    ) -> AnalysisSnapshotRecord:
        """写入已确认的 AnalysisSnapshot，并回写 Suggestion.current_analysis_id。

        对应 backend：
        - `AnalysisRepository.get_by_run_id`（幂等闸门）
        - `AnalysisRepository.create_snapshot`
        - `SuggestionRepository.set_current_analysis`
        - `SuggestionRepository.update_suggestion_status`（SUCCEEDED）
        - 审计：`AuditRepository.create`（分类人工确认时）
        组合逻辑见 `graphs/analysis_persist.py::persist_analysis` 中快照部分。
        """
        ...

    def save_modification_facts(
        self,
        *,
        suggestion_id: UUID,
        workspace_id: UUID,
        input_version: str,
        user_id: str,
        fact_proposals: list[dict[str, Any]],
    ) -> list[ModificationFactRecord]:
        """批量写入已确认的 ModificationFact。

        对应 backend：
        - `AnalysisRepository.create_fact`（逐条）
        - `AuditRepository.create`（每条 FACT CONFIRM）
        组合逻辑见 `graphs/analysis_persist.py::persist_analysis` 中事实部分。
        """
        ...

    def save_analysis_result(
        self,
        *,
        suggestion_id: UUID,
        workspace_id: UUID,
        run_id: UUID,
        input_version: str,
        user_id: str,
        classification: dict[str, Any],
        evidence: dict[str, Any],
        priority: dict[str, Any],
        recommended_actions: dict[str, Any] | list[dict[str, Any]],
        fact_proposals: list[dict[str, Any]],
        classification_confirmed_by_user: bool = False,
    ) -> SaveAnalysisResult:
        """单事务写入快照 + 修改事实（推荐实现路径）。

        对应 backend：`graphs/analysis_persist.py::persist_analysis` 全量。
        实现可内部复用 `save_analysis_snapshot` / `save_modification_facts`，
        但必须保证同一业务事务与 run_id 幂等。
        """
        ...
