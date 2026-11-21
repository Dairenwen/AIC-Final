"""论文信息卡片数据模型。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from langgraph_agent.adapters.postgres.models import Base

# 与 tools/paper_schemas 的 CardType / ConfirmationStatus 取值对齐；
# adapter 层不依赖 tools，避免 Wave1 循环依赖。
_CARD_TYPE_VALUES = (
    "'RESEARCH_QUESTION', 'RESEARCH_MOTIVATION', 'CORE_CONTRIBUTIONS', "
    "'MAIN_METHOD', 'DATASET_OR_SAMPLE', 'EXPERIMENT_SETUP_BASELINES_METRICS', "
    "'MAIN_RESULTS', 'ABLATION_OR_SUPPLEMENTARY_ANALYSIS', 'LIMITATIONS', "
    "'RESEARCH_BOUNDARY'"
)
_CONFIRMATION_STATUS_VALUES = "'PENDING', 'CONFIRMED', 'EDITED', 'DELETED'"


class PaperCardRecord(Base):
    """慢速模式论文信息卡片（每张卡片对应一类提取结论）。"""

    __tablename__ = "paper_cards"
    __table_args__ = (
        CheckConstraint(
            f"card_type IN ({_CARD_TYPE_VALUES})",
            name="ck_paper_cards_card_type",
        ),
        CheckConstraint(
            f"confirmation_status IN ({_CONFIRMATION_STATUS_VALUES})",
            name="ck_paper_cards_confirmation_status",
        ),
        Index(
            "ix_paper_cards_workspace_manuscript",
            "workspace_id",
            "manuscript_version_id",
        ),
        Index(
            "ix_paper_cards_workspace_status",
            "workspace_id",
            "confirmation_status",
        ),
    )

    paper_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    manuscript_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    card_type: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_sections: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    source_quote: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    confirmation_status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
