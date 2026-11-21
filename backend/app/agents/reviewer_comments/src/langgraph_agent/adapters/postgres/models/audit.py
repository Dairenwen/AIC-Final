"""人工决策审计与图运行记录数据模型。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from langgraph_agent.adapters.postgres.models import Base


class DecisionEvent(Base):
    """需要在业务对象清理后继续保留的人工决策审计事件。"""

    __tablename__ = "decision_events"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('SUGGESTION', 'SOURCE', 'ANALYSIS', 'FACT', 'REPLY', "
            "'DRAFT', 'EXPORT')",
            name="ck_decision_events_target_type",
        ),
        CheckConstraint(
            "action IN ('CONFIRM', 'REJECT', 'EDIT', 'REOPEN', 'SUPERSEDE', 'CANCEL')",
            name="ck_decision_events_action",
        ),
        Index("ix_decision_events_workspace_created", "workspace_id", "created_at"),
        Index("ix_decision_events_target", "target_type", "target_id"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GraphRun(Base):
    """三个 LangGraph 图的幂等运行记录。"""

    __tablename__ = "graph_runs"
    __table_args__ = (
        CheckConstraint(
            "graph_name IN ('WorkspaceTaskGraph', 'SuggestionAnalysisGraph', "
            "'SourceReplyGraph')",
            name="ck_graph_runs_graph_name",
        ),
        CheckConstraint(
            "target_type IN ('WORKSPACE', 'SUGGESTION', 'SOURCE')",
            name="ck_graph_runs_target_type",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'WAITING_USER', 'SUCCEEDED', "
            "'FAILED_RETRYABLE', 'FAILED_FINAL', 'SUPERSEDED', 'CANCELLED')",
            name="ck_graph_runs_status",
        ),
        Index(
            "uq_graph_runs_idempotency",
            "graph_name",
            "target_id",
            "input_version",
            "attempt",
            unique=True,
        ),
        Index("ix_graph_runs_workspace_status", "workspace_id", "status"),
        Index("ix_graph_runs_thread_id", "thread_id"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    graph_name: Mapped[str] = mapped_column(Text, nullable=False)
    thread_id: Mapped[str] = mapped_column(String, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    input_version: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(Text)
    result_refs: Mapped[list[dict[str, Any]] | list[uuid.UUID]] = mapped_column(
        JSONB, nullable=False
    )
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
