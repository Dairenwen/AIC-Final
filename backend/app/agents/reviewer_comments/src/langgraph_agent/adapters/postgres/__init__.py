"""PostgreSQL 默认适配器（1A）。

与 backend 可共用同一 DB；本包在 langgraph-agent/migrations 维护兼容 revision 链，
交付后可用 ``python scripts/init_db.py`` 独立建表。
"""

from langgraph_agent.adapters.postgres.base import (
    add_and_flush,
    get_by_pk,
    session_scope,
)
from langgraph_agent.adapters.postgres.db import create_session_factory, get_engine
from langgraph_agent.adapters.postgres.stores import (
    PostgresAnalysisStore,
    PostgresFinalizeStore,
    PostgresManuscriptStore,
    PostgresReplyStore,
    PostgresRunStore,
    PostgresSuggestionStore,
    PostgresWorkspaceStore,
    build_postgres_stores,
)

__all__ = [
    "PostgresAnalysisStore",
    "PostgresFinalizeStore",
    "PostgresManuscriptStore",
    "PostgresReplyStore",
    "PostgresRunStore",
    "PostgresSuggestionStore",
    "PostgresWorkspaceStore",
    "add_and_flush",
    "build_postgres_stores",
    "create_session_factory",
    "get_by_pk",
    "get_engine",
    "session_scope",
]
