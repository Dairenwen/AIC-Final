"""端口层薄 DTO / TypedDict。

故意不依赖 `langgraph_agent.schemas`，避免 Wave1 并行时与 A3 循环依赖。
Adapter（A5）负责 ORM 实体 ↔ 这些字典的转换；图节点只消费纯数据。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict
from uuid import UUID


class WorkspaceRecord(TypedDict):
    """对应 `Workspace` 表的只读视图。"""

    workspace_id: UUID
    user_id: str
    title: str
    mode: str
    status: str
    global_settings: dict[str, Any]
    schema_version: int


class ReviewPartyRecord(TypedDict):
    """对应 `ReviewParty`。"""

    party_id: UUID
    workspace_id: UUID
    role: str
    display_name: str
    raw_label: str


class ReviewInputRecord(TypedDict):
    """对应 `ReviewInput`，可附带来源方展示字段。"""

    review_input_id: UUID
    workspace_id: UUID
    party_id: UUID
    version_no: int
    raw_text: str | None
    storage_uri: str | None
    content_hash: str
    language: str | None
    is_current: bool
    # 下列字段由 list_current_review_inputs 拼装，便于 load_inputs 直接使用
    role: str
    display_name: str
    raw_label: str


class SuggestionSourceRecord(TypedDict):
    """对应 `SuggestionSource` 的精简视图。"""

    source_id: UUID
    suggestion_id: UUID
    workspace_id: UUID
    party_id: UUID
    review_input_id: UUID
    excerpt: str
    content_hash: str
    localized_claim: str
    stance: str | None
    span_refs: dict[str, Any] | list[Any]
    status: str
    expression_settings_override: dict[str, Any] | None


class SuggestionRecord(TypedDict):
    """对应 `Suggestion`。"""

    suggestion_id: UUID
    workspace_id: UUID
    canonical_text: str
    status: str
    merge_group_key: str | None
    conflict_group_key: str | None
    priority: str | None
    category_ids: list[str]
    input_version: str
    current_analysis_id: UUID | None


class SuggestionBundle(TypedDict):
    """分析/回复共用：建议本体 + 来源列表。"""

    suggestion: SuggestionRecord
    sources: list[SuggestionSourceRecord]


class AnalysisSnapshotRecord(TypedDict):
    """对应 `AnalysisSnapshot`。"""

    analysis_id: UUID
    suggestion_id: UUID
    workspace_id: UUID
    run_id: UUID
    input_version: str
    categories: dict[str, Any] | list[Any]
    evidence_items: list[dict[str, Any]]
    coverage: str
    priority: str
    recommended_actions: list[dict[str, Any]] | dict[str, Any]
    confidence: float | None
    status: str
    confirmed_at: datetime | None
    confirmed_by: str | None


class ModificationFactRecord(TypedDict):
    """对应 `ModificationFact`。"""

    fact_id: UUID
    suggestion_id: UUID
    workspace_id: UUID
    action_type: str
    paper_change_summary: str
    response_fact_summary: str
    constraints: dict[str, Any] | list[Any]
    status: str
    input_version: str
    confirmed_at: datetime | None
    confirmed_by: str | None


class PaperSectionRecord(TypedDict, total=False):
    """论文 structure_summary.sections 条目。"""

    original_heading: str
    normalized_type: str
    pages: list[Any]
    confidence: float | None


class PaperCardRecord(TypedDict):
    """对应 `PaperCardRecord`。"""

    paper_card_id: UUID
    workspace_id: UUID
    manuscript_version_id: UUID
    card_type: str
    content: str
    source_sections: list[Any]
    source_quote: str
    confidence: float
    confirmation_status: str


class ManuscriptVersionRecord(TypedDict):
    """对应 `ManuscriptVersion`。"""

    manuscript_version_id: UUID
    workspace_id: UUID
    version_no: int
    source_type: str
    storage_uri: str
    content_hash: str
    parse_status: str
    structure_summary: dict[str, Any]
    is_baseline: bool


class PaperBaseline(TypedDict):
    """分析图 `_load_paper_baseline` 形态的论文基线。"""

    has_baseline: bool
    manuscript_version_id: str | None
    abstract: str
    sections: list[dict[str, Any]]
    cards: list[dict[str, Any]]


class AnalysisContext(TypedDict):
    """分析图加载上下文：建议包 + 可复用快照 + 论文基线。"""

    suggestion: SuggestionRecord
    sources: list[SuggestionSourceRecord]
    current_snapshot: AnalysisSnapshotRecord | None
    confirmed_facts: list[ModificationFactRecord]
    paper_baseline: PaperBaseline
    reusable: bool


class SourceReplyRecord(TypedDict):
    """对应 `SourceReply`。"""

    reply_id: UUID
    source_id: UUID
    suggestion_id: UUID
    workspace_id: UUID
    strategy: dict[str, Any]
    expression_settings: dict[str, Any]
    response_facts: list[dict[str, Any]] | dict[str, Any] | list[Any]
    status: str
    current_draft_id: UUID | None
    input_version: str


class ReplyDraftRecord(TypedDict):
    """对应 `ReplyDraftVersion`。"""

    draft_id: UUID
    reply_id: UUID
    version_no: int
    content: str
    language: str
    consistency_report: dict[str, Any] | list[Any]
    status: str
    run_id: UUID | None
    approved_at: datetime | None
    approved_by: str | None


class ApprovedSourceReplyView(TypedDict):
    """同建议下其他已批准回复（供策略/一致性参考）。"""

    source_id: UUID
    generated_content: str
    linked_fact_ids: list[Any]


class ReplyContext(TypedDict):
    """回复图 `load_context` 形态。"""

    analysis_ready: bool
    source: SuggestionSourceRecord
    expression_settings: dict[str, Any]
    confirmed_analysis: AnalysisSnapshotRecord | None
    confirmed_modification_facts: list[ModificationFactRecord]
    other_approved_replies: list[ApprovedSourceReplyView]


class ResultRef(TypedDict):
    """与 backend `ResultReference` JSON 对齐的最小结构。"""

    type: str
    id: str


class PersistTaskInitResult(TypedDict):
    """`persist_and_ready` 落库结果。"""

    result_refs: list[ResultRef]
    workspace_status: str


class SaveAnalysisResult(TypedDict):
    """分析快照 + 修改事实联合落库结果。"""

    snapshot: AnalysisSnapshotRecord
    facts: list[ModificationFactRecord]
    result_refs: list[ResultRef]
    reused: bool


class SaveReplyDraftResult(TypedDict):
    """`persist_and_review` 落库结果。"""

    reply: SourceReplyRecord
    draft: ReplyDraftRecord
    result_refs: list[ResultRef]
    phase: str
    reused: bool


class SaveReviewDecisionResult(TypedDict):
    """`persist_review_decision` 落库结果。"""

    reply: SourceReplyRecord
    draft: ReplyDraftRecord
    result_refs: list[ResultRef]
    phase: str
    synced_sources: list[Any]


class GraphRunRecord(TypedDict):
    """对应 `GraphRun`。"""

    run_id: UUID
    workspace_id: UUID
    graph_name: str
    thread_id: str
    target_type: str
    target_id: UUID
    input_version: str
    status: str
    attempt: int
    error_code: str | None
    error_message: str | None
    result_refs: list[dict[str, Any]] | list[Any]
    parent_run_id: UUID | None
    started_at: datetime | None
    finished_at: datetime | None
