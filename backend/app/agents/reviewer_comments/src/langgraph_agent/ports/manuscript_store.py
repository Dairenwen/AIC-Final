"""论文版本与信息卡片端口。"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from langgraph_agent.ports.types import ManuscriptVersionRecord, PaperCardRecord


class ManuscriptStore(Protocol):
    """论文版本与基线卡片端口（SLOW 模式相关）。"""

    def get_manuscript_version(
        self, manuscript_version_id: UUID
    ) -> ManuscriptVersionRecord | None:
        """按主键读取论文版本。

        对应 backend：`ManuscriptRepository.get_by_id`。
        调用方语义对齐 `manuscript_node.parse_manuscript`。
        """
        ...

    def get_paper_cards(
        self,
        workspace_id: UUID,
        manuscript_version_id: UUID,
        *,
        confirmed_only: bool = False,
    ) -> list[PaperCardRecord]:
        """读取论文信息卡片。

        对应 backend：
        - `confirmed_only=False` → `PaperCardRepository.list_by_manuscript`
          （对齐 `generate_baseline_cards` 读 PENDING 候选）
        - `confirmed_only=True` → `PaperCardRepository.list_confirmed`
          （对齐分析图 `_load_paper_baseline`）
        """
        ...

    def save_baseline_cards(
        self,
        *,
        workspace_id: UUID,
        manuscript_version_id: UUID,
        confirmed_cards: list[dict[str, Any]],
    ) -> list[PaperCardRecord]:
        """将确认后的基线卡片写回，并把该版本标为 baseline。

        对应 backend：
        - `PaperCardRepository.create`（action=create）
        - `PaperCardRepository.update_confirmation`（action=update）
        - `ManuscriptRepository.set_baseline`
        组合逻辑：`graphs/manuscript_node.py::persist_baseline`。
        `confirmed_cards` 每项需含 `action` 及 create/update 所需字段。
        """
        ...
