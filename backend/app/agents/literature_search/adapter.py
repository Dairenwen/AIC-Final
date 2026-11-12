from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


CORE_ROOT = Path(__file__).resolve().parent / "core"
MATPLOTLIB_CACHE = Path(tempfile.gettempdir()) / "zhiyan-matplotlib"
MATPLOTLIB_CACHE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MATPLOTLIB_CACHE))


class LiteratureCoreAdapter:
    """Loads the vendored Agent Core without depending on the old project path."""

    def __init__(self) -> None:
        core_path = str(CORE_ROOT)
        if core_path not in sys.path:
            sys.path.insert(0, core_path)
        agent = importlib.import_module("src.agent")
        schemas = importlib.import_module("src.schemas")
        tools = importlib.import_module("src.tools")
        self._build_graph = agent.build_literature_graph
        self._runtime_context = schemas.LiteratureRuntimeContext
        self.arxiv_tool = tools.ArxivSearchTool
        self.scholar_tool = tools.GoogleScholarSearchTool

    def build_graph(self, **kwargs: Any):
        return self._build_graph(**kwargs)

    def runtime_context(self, *, user_id: str, thread_id: str):
        return self._runtime_context(user_id=user_id, thread_id=thread_id)

    def external_tools(self, enabled: bool) -> tuple[Any, Any]:
        if not enabled:
            return EmptyRetriever(), EmptyRetriever()
        return self.arxiv_tool(), self.scholar_tool()


class EmptyRetriever:
    def invoke(self, _input: dict[str, Any]) -> dict[str, list[Any]]:
        return {"papers": []}
