from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, SmallInteger, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Task(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "tasks"
    __table_args__ = {"schema": "zhiyan"}

    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    conversation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    agent_team_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    model_config_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", nullable=False)
    progress: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    current_step: Mapped[str | None] = mapped_column(String(150))
    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    trace_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    retry_count: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    error_code: Mapped[str | None] = mapped_column(String(100))
    safe_error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskEvent(UUIDPrimaryKeyMixin, db.Model):
    __tablename__ = "task_events"
    __table_args__ = {"schema": "zhiyan"}

    task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zhiyan.tasks.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PaperReadingRun(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "paper_reading_runs"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_paper_reading_runs_task_id"),
        {"schema": "zhiyan"},
    )

    task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zhiyan.tasks.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    agent_version: Mapped[str] = mapped_column(String(32), default="0.6.4", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="QUEUED", nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    speed_profile: Mapped[str] = mapped_column(String(20), default="balanced", nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(20), default="flow_first", nullable=False)
    paper_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    reading_result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    scientific_analysis: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    experiment_analysis: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    reliability: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    flow_execution: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    timing: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    artifacts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PatentDraftingRun(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "patent_drafting_runs"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_patent_drafting_runs_task_id"),
        {"schema": "zhiyan"},
    )

    task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zhiyan.tasks.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(40), default="QUEUED", nullable=False)
    workflow_mode: Mapped[str] = mapped_column(String(20), default="flow_first", nullable=False)
    source_file_name: Mapped[str | None] = mapped_column(String(255))
    selected_candidate_id: Mapped[str | None] = mapped_column(String(80))
    selection_notes: Mapped[str | None] = mapped_column(Text)
    candidates: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    run_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    artifacts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    waiting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AcademicFigureRun(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "academic_figure_runs"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_academic_figure_runs_task_id"),
        {"schema": "zhiyan"},
    )

    task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zhiyan.tasks.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), default="QUEUED", nullable=False)
    planning_mode: Mapped[str] = mapped_column(String(20), default="online", nullable=False)
    figure_type: Mapped[str] = mapped_column(String(30), default="auto", nullable=False)
    input_files: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    figure_spec: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    dataset_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    captions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    quality_report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    artifacts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ArxivDailyRun(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "arxiv_daily_runs"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_arxiv_daily_runs_task_id"),
        {"schema": "zhiyan"},
    )

    task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zhiyan.tasks.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), default="QUEUED", nullable=False)
    category: Mapped[str] = mapped_column(String(20), default="cs.AI", nullable=False, index=True)
    category_name: Mapped[str] = mapped_column(String(100), default="人工智能", nullable=False)
    search_query: Mapped[str | None] = mapped_column(String(200))
    refresh_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_url: Mapped[str] = mapped_column(String(500), default="https://www.arxivdaily.com/", nullable=False)
    paper_count: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    categories: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    papers: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
