"""SQLAlchemy 业务表模型与声明基类。

与 backend 共用同一 PostgreSQL 实例；本包在 langgraph-agent/migrations 维护
与 backend 兼容的 Alembic revision 链，交付后可独立建表。
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有业务表的声明基类。"""


# 放在 Base 定义之后导入，确保元数据可被发现。
from langgraph_agent.adapters.postgres.models.analysis import (  # noqa: E402
    AnalysisSnapshot,
    ModificationFact,
)
from langgraph_agent.adapters.postgres.models.audit import (  # noqa: E402
    DecisionEvent,
    GraphRun,
)
from langgraph_agent.adapters.postgres.models.manuscript import (  # noqa: E402
    ManuscriptVersion,
)
from langgraph_agent.adapters.postgres.models.paper_card import (  # noqa: E402
    PaperCardRecord,
)
from langgraph_agent.adapters.postgres.models.reply import (  # noqa: E402
    ReplyDraftVersion,
    SourceReply,
)
from langgraph_agent.adapters.postgres.models.review import (  # noqa: E402
    ReviewInput,
    ReviewParty,
)
from langgraph_agent.adapters.postgres.models.suggestion import (  # noqa: E402
    Suggestion,
    SuggestionSource,
)
from langgraph_agent.adapters.postgres.models.workspace import Workspace  # noqa: E402

__all__ = [
    "AnalysisSnapshot",
    "Base",
    "DecisionEvent",
    "GraphRun",
    "ManuscriptVersion",
    "ModificationFact",
    "PaperCardRecord",
    "ReplyDraftVersion",
    "ReviewInput",
    "ReviewParty",
    "SourceReply",
    "Suggestion",
    "SuggestionSource",
    "Workspace",
]
