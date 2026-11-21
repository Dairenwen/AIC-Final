"""图流回归总入口。

汇总引用各 flow 测试模块，并提供四图「可编译」的轻量冒烟。
详细路径 / interrupt 断言仍在：
- test_graph_flow_workspace
- test_graph_flow_analysis
- test_graph_flow_reply
- test_finalize
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver, MemorySaver

_TESTS_DIR = Path(__file__).resolve().parent

# 汇总入口：确保各 flow / 节点测试模块可被本文件引用。
# 不 re-export 用例函数，避免 pytest 双重收集。
_FLOW_TEST_MODULES = (
    "test_graph_flow_workspace",
    "test_graph_flow_analysis",
    "test_graph_flow_reply",
    "test_finalize",
    "test_workspace_task_nodes",
    "test_analysis_nodes",
    "test_reply_nodes",
)


def _load_test_module(module_name: str):
    path = _TESTS_DIR / f"{module_name}.py"
    assert path.is_file(), f"缺少 flow 测试文件：{path}"
    # 保证同目录测试互引用时能找到兄弟模块
    tests_dir = str(_TESTS_DIR)
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("module_name", _FLOW_TEST_MODULES)
def test_flow_test_module_importable(module_name: str) -> None:
    module = _load_test_module(module_name)
    assert module is not None
    assert hasattr(module, "__file__")


def test_workspace_task_graph_compiles() -> None:
    from langgraph_agent.agent.workspace_task.graph import (
        WorkspaceTaskStores,
        build_workspace_task_graph,
    )

    class _WS:
        def get_workspace(self, workspace_id):  # noqa: ANN001
            return {
                "workspace_id": workspace_id,
                "user_id": "u",
                "title": "t",
                "mode": "FAST",
                "status": "ACTIVE",
                "global_settings": {},
                "schema_version": 1,
            }

        def list_current_review_inputs(self, workspace_id):  # noqa: ANN001
            return []

        def list_parties(self, workspace_id):  # noqa: ANN001
            return []

        def persist_task_init_result(self, **kwargs: Any):  # noqa: ANN003
            return {"result_refs": [], "workspace_status": "ACTIVE"}

    graph = build_workspace_task_graph(
        stores=WorkspaceTaskStores(workspace=_WS()),  # type: ignore[arg-type]
        checkpointer=InMemorySaver(),
    )
    assert graph is not None
    assert len(graph.get_graph().nodes) >= 3


def test_suggestion_analysis_graph_compiles() -> None:
    from langgraph_agent.agent.analysis.graph import build_suggestion_analysis_graph

    class _AnalysisStore:
        pass

    graph = build_suggestion_analysis_graph(
        stores={"analysis_store": _AnalysisStore()},
        checkpointer=InMemorySaver(),
    )
    assert graph is not None


def test_source_reply_graph_compiles() -> None:
    from langgraph_agent.agent.reply.graph import build_source_reply_graph

    class _ReplyStore:
        def load_reply_context(self, **kwargs: Any):  # noqa: ANN003
            raise NotImplementedError

        def save_reply_draft(self, **kwargs: Any):  # noqa: ANN003
            raise NotImplementedError

        def save_review_decision(self, **kwargs: Any):  # noqa: ANN003
            raise NotImplementedError

    graph = build_source_reply_graph(
        stores=_ReplyStore(),  # type: ignore[arg-type]
        checkpointer=MemorySaver(),
    )
    assert graph is not None


def test_finalize_graph_compiles() -> None:
    from langgraph_agent.agent.finalize import build_finalize_graph

    class _FinalizeStore:
        def load_finalize_context(self, workspace_id):  # noqa: ANN001
            return {
                "workspace_id": str(workspace_id or uuid4()),
                "workspace_title": "汇总冒烟",
                "user_id": "user-1",
                "global_settings": {},
                "suggestions": [],
                "sources": [],
                "internal_revision_items": [],
                "external_replies": [],
            }

        def create_export_snapshot(self, **kwargs: Any):  # noqa: ANN003
            return {"export_snapshot_id": str(uuid4())}

        def save_export_files(self, **kwargs: Any):  # noqa: ANN003
            return {"files": []}

        def mark_export_completed(self, **kwargs: Any):  # noqa: ANN003
            return None

    graph = build_finalize_graph(stores={"finalize": _FinalizeStore()})
    assert graph is not None
