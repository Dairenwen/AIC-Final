"""建议分析快照与修改事实数据模型。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from langgraph_agent.adapters.postgres.models import Base


class AnalysisSnapshot(Base):
    """一次建议分析运行产生的不可变快照。"""

    __tablename__ = "analysis_snapshots"
    __table_args__ = (
        CheckConstraint(
            "coverage IN ('FULL', 'PARTIAL', 'NONE', 'UNKNOWN')",
            name="ck_analysis_snapshots_coverage",
        ),
        CheckConstraint(
            "priority IN ('P0', 'P1', 'P2', 'P3')",
            name="ck_analysis_snapshots_priority",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'CONFIRMED', 'STALE', 'SUPERSEDED')",
            name="ck_analysis_snapshots_status",
        ),
        Index("ix_analysis_snapshots_suggestion_status", "suggestion_id", "status"),
        Index("uq_analysis_snapshots_run_id", "run_id", unique=True),
    )

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    suggestion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suggestions.suggestion_id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    input_version: Mapped[str] = mapped_column(String, nullable=False)
    categories: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSONB, nullable=False)
    evidence_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    coverage: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_actions: Mapped[list[dict[str, Any]] | dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModificationFact(Base):
    """经用户确认、可供回复引用的论文修改事实。"""

    __tablename__ = "modification_facts"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ('ACCEPT', 'PARTIAL_ACCEPT', 'REJECT', 'CLARIFY', 'DEFER')",
            name="ck_modification_facts_action_type",
        ),
        CheckConstraint(
            "status IN ('CONFIRMED', 'STALE', 'SUPERSEDED')",
            name="ck_modification_facts_status",
        ),
        Index("ix_modification_facts_suggestion_status", "suggestion_id", "status"),
    )

    fact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    suggestion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suggestions.suggestion_id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    paper_change_summary: Mapped[str] = mapped_column(Text, nullable=False)
    response_fact_summary: Mapped[str] = mapped_column(Text, nullable=False)
    constraints: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    input_version: Mapped[str] = mapped_column(String, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
