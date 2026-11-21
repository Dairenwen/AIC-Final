"""Postgres adapter 冒烟测试。

纯 import 用例始终运行；需真实数据库的连通用例标 integration，
并在无 DATABASE_URL 时 skip。
"""

from __future__ import annotations

import os

import pytest

_HAS_DATABASE_URL = bool(os.getenv("DATABASE_URL", "").strip())


def test_import_postgres_adapter() -> None:
    import langgraph_agent.adapters.postgres as pg

    assert hasattr(pg, "PostgresWorkspaceStore")
    assert hasattr(pg, "build_postgres_stores")
    assert hasattr(pg, "create_session_factory")
    assert hasattr(pg, "get_engine")
    assert hasattr(pg, "session_scope")


def test_models_and_repositories_import() -> None:
    from langgraph_agent.adapters.postgres import models, repositories

    assert models.Workspace.__tablename__ == "workspaces"
    assert models.Suggestion.__tablename__ == "suggestions"
    assert models.GraphRun.__tablename__ == "graph_runs"
    assert models.PaperCardRecord.__tablename__ == "paper_cards"
    assert repositories.WorkspaceRepository is not None
    assert repositories.SuggestionRepository is not None
    assert repositories.GraphRunRepository is not None


def test_stores_cover_a4_protocols() -> None:
    """A5 stores 方法集覆盖 A4 Protocol（不实例化，不连库）。"""
    from langgraph_agent.adapters.postgres.stores import (
        PostgresAnalysisStore,
        PostgresManuscriptStore,
        PostgresReplyStore,
        PostgresRunStore,
        PostgresSuggestionStore,
        PostgresWorkspaceStore,
    )
    from langgraph_agent.ports import (
        AnalysisStore,
        ManuscriptStore,
        ReplyStore,
        RunStore,
        SuggestionStore,
        WorkspaceStore,
    )

    pairs = [
        (PostgresWorkspaceStore, WorkspaceStore),
        (PostgresSuggestionStore, SuggestionStore),
        (PostgresAnalysisStore, AnalysisStore),
        (PostgresReplyStore, ReplyStore),
        (PostgresManuscriptStore, ManuscriptStore),
        (PostgresRunStore, RunStore),
    ]
    for impl, proto in pairs:
        required = set(getattr(proto, "__protocol_attrs__", ()))
        if not required:
            required = {
                name
                for name, value in vars(proto).items()
                if not name.startswith("_") and callable(value)
            }
        provided = {
            name
            for base in impl.__mro__
            for name, value in vars(base).items()
            if not name.startswith("_") and callable(value)
        }
        missing = sorted(required - provided)
        assert not missing, f"{impl.__name__} 缺少 Protocol 方法：{missing}"


@pytest.mark.integration
@pytest.mark.skipif(not _HAS_DATABASE_URL, reason="未配置 DATABASE_URL，跳过数据库集成测试")
def test_engine_connects_when_database_url_set() -> None:
    from sqlalchemy import text

    from langgraph_agent.adapters.postgres.db import create_session_factory, get_engine

    engine = get_engine()
    with engine.connect() as conn:
        value = conn.execute(text("SELECT 1")).scalar()
    assert value == 1

    factory = create_session_factory(engine)
    session = factory()
    try:
        assert session.execute(text("SELECT 1")).scalar() == 1
    finally:
        session.close()


@pytest.mark.integration
@pytest.mark.skipif(not _HAS_DATABASE_URL, reason="未配置 DATABASE_URL，跳过数据库集成测试")
def test_build_postgres_stores_constructs() -> None:
    from langgraph_agent.adapters.postgres import build_postgres_stores
    from langgraph_agent.adapters.postgres.db import create_session_factory

    stores = build_postgres_stores(create_session_factory())
    assert set(stores) >= {
        "workspace",
        "suggestion",
        "analysis",
        "reply",
        "manuscript",
        "run",
    }
