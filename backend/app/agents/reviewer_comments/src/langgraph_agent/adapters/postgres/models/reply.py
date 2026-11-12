"""来源级回复与草稿版本数据模型。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from langgraph_agent.adapters.postgres.models import Base


class SourceReply(Base):
    """针对单个建议来源独立维护的回复。"""

    __tablename__ = "source_replies"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'STRATEGY_WAITING', 'FACTS_WAITING', 'DRAFTING', "
            "'REVIEW_WAITING', 'APPROVED', 'FAILED_RETRYABLE', 'FAILED_FINAL', "
            "'STALE', 'SUPERSEDED', 'CANCELLED')",
            name="ck_source_replies_status",
        ),
        UniqueConstraint("source_id", name="uq_source_replies_source_id"),
        Index("ix_source_replies_suggestion_id", "suggestion_id"),
    )

    reply_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suggestion_sources.source_id", ondelete="CASCADE"),
        nullable=False,
    )
    suggestion_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    strategy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expression_settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    response_facts: Mapped[list[dict[str, Any]] | list[uuid.UUID]] = mapped_column(
        JSONB, nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    current_draft_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    input_version: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ReplyDraftVersion(Base):
    """来源级回复的版本化草稿。"""

    __tablename__ = "reply_draft_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('GENERATED', 'EDITED', 'APPROVED', 'REJECTED', 'STALE')",
            name="ck_reply_draft_versions_status",
        ),
        UniqueConstraint(
            "reply_id", "version_no", name="uq_reply_draft_versions_reply_version"
        ),
        Index("ix_reply_draft_versions_reply_status", "reply_id", "status"),
    )

    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    reply_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_replies.reply_id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String, nullable=False)
    consistency_report: Mapped[dict[str, Any] | list[Any]] = mapped_column(
        JSONB, nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String)
