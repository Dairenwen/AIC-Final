"""适配器实现。

默认导出 Postgres（1A）能力；其它 adapter 后续按需扩展。
"""

from langgraph_agent.adapters.postgres import (
    PostgresAnalysisStore,
    PostgresManuscriptStore,
    PostgresReplyStore,
    PostgresRunStore,
    PostgresSuggestionStore,
    PostgresWorkspaceStore,
    build_postgres_stores,
    create_session_factory,
    get_engine,
    session_scope,
)

__all__ = [
    "PostgresAnalysisStore",
    "PostgresManuscriptStore",
    "PostgresReplyStore",
    "PostgresRunStore",
    "PostgresSuggestionStore",
    "PostgresWorkspaceStore",
    "build_postgres_stores",
    "create_session_factory",
    "get_engine",
    "session_scope",
]
