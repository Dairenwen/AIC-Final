"""持久化与上下文加载端口（仅 Protocol，无实现）。

图节点通过本包注入的 Store 访问数据；默认 Postgres 实现由 A5 提供。
"""

from langgraph_agent.ports.analysis_store import AnalysisStore
from langgraph_agent.ports.finalize_store import (
    ExportSnapshotRecord,
    FinalizeContext,
    FinalizeStore,
)
from langgraph_agent.ports.manuscript_store import ManuscriptStore
from langgraph_agent.ports.reply_store import ReplyStore
from langgraph_agent.ports.run_store import RunStore
from langgraph_agent.ports.suggestion_store import SuggestionStore
from langgraph_agent.ports.types import (
    AnalysisContext,
    AnalysisSnapshotRecord,
    ApprovedSourceReplyView,
    GraphRunRecord,
    ManuscriptVersionRecord,
    ModificationFactRecord,
    PaperBaseline,
    PaperCardRecord,
    PaperSectionRecord,
    PersistTaskInitResult,
    ReplyContext,
    ReplyDraftRecord,
    ResultRef,
    ReviewInputRecord,
    ReviewPartyRecord,
    SaveAnalysisResult,
    SaveReplyDraftResult,
    SaveReviewDecisionResult,
    SourceReplyRecord,
    SuggestionBundle,
    SuggestionRecord,
    SuggestionSourceRecord,
    WorkspaceRecord,
)
from langgraph_agent.ports.workspace_store import WorkspaceStore

__all__ = [
    # Protocols
    "AnalysisStore",
    "FinalizeStore",
    "ManuscriptStore",
    "ReplyStore",
    "RunStore",
    "SuggestionStore",
    "WorkspaceStore",
    # DTO / TypedDict
    "AnalysisContext",
    "AnalysisSnapshotRecord",
    "ApprovedSourceReplyView",
    "ExportSnapshotRecord",
    "FinalizeContext",
    "GraphRunRecord",
    "ManuscriptVersionRecord",
    "ModificationFactRecord",
    "PaperBaseline",
    "PaperCardRecord",
    "PaperSectionRecord",
    "PersistTaskInitResult",
    "ReplyContext",
    "ReplyDraftRecord",
    "ResultRef",
    "ReviewInputRecord",
    "ReviewPartyRecord",
    "SaveAnalysisResult",
    "SaveReplyDraftResult",
    "SaveReviewDecisionResult",
    "SourceReplyRecord",
    "SuggestionBundle",
    "SuggestionRecord",
    "SuggestionSourceRecord",
    "WorkspaceRecord",
]
