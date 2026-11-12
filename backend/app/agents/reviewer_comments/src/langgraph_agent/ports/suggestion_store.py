"""Suggestion 读取端口。"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from langgraph_agent.ports.types import SuggestionBundle


class SuggestionStore(Protocol):
    """共享建议与来源的只读加载端口。"""

    def load_suggestion_bundle(
        self,
        suggestion_id: UUID,
        *,
        workspace_id: UUID | None = None,
        source_status: str | None = "ACTIVE",
    ) -> SuggestionBundle:
        """加载建议本体及其来源列表。

        对应 backend：
        - `SuggestionRepository.get_suggestion`
        - `SuggestionRepository.list_sources`（默认仅 ACTIVE）
        调用方语义对齐 `analysis_graph.load_suggestion`；
        若传入 `workspace_id`，实现应校验归属一致性。
        """
        ...
