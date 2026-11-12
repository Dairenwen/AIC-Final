"""共享建议及其来源数据模型。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from langgraph_agent.adapters.postgres.models import Base


class Suggestion(Base):
    """供多个审稿来源共享的一条归一化建议。"""

    __tablename__ = "suggestions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'WAITING_USER', 'READY_FOR_REPLY', "
            "'SUCCEEDED', 'FAILED_RETRYABLE', 'FAILED_FINAL', 'STALE', "
            "'SUPERSEDED', 'CANCELLED')",
            name="ck_suggestions_status",
        ),
        CheckConstraint(
            "priority IS NULL OR priority IN ('P0', 'P1', 'P2', 'P3')",
            name="ck_suggestions_priority",
        ),
        Index("ix_suggestions_workspace_status", "workspace_id", "status"),
        Index(
            "ix_suggestions_workspace_merge_group",
            "workspace_id",
            "merge_group_key",
        ),
    )

    suggestion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    canonical_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    merge_group_key: Mapped[str | None] = mapped_column(String)
    conflict_group_key: Mapped[str | None] = mapped_column(String)
    priority: Mapped[str | None] = mapped_column(Text)
    category_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    input_version: Mapped[str] = mapped_column(String, nullable=False)
    current_analysis_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SuggestionSource(Base):
    """共享建议对应的单个审稿来源与具体诉求。"""

    __tablename__ = "suggestion_sources"
    __table_args__ = (
        CheckConstraint(
            "stance IS NULL OR stance IN ('REQUEST', 'CONCERN', 'PRAISE', 'ADMIN')",
            name="ck_suggestion_sources_stance",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'SUPERSEDED', 'IGNORED')",
            name="ck_suggestion_sources_status",
        ),
        UniqueConstraint(
            "suggestion_id",
            "party_id",
            "content_hash",
            name="uq_suggestion_sources_suggestion_party_hash",
        ),
        Index("ix_suggestion_sources_suggestion_id", "suggestion_id"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    suggestion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suggestions.suggestion_id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    party_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    review_input_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    localized_claim: Mapped[str] = mapped_column(Text, nullable=False)
    stance: Mapped[str | None] = mapped_column(Text)
    span_refs: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSONB, nullable=False)
    expression_settings_override: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
