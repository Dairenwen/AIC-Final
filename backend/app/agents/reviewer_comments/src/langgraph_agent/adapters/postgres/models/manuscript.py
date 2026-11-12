"""论文版本数据模型。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from langgraph_agent.adapters.postgres.models import Base


class ManuscriptVersion(Base):
    """不可原地覆盖的论文版本。"""

    __tablename__ = "manuscript_versions"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('UPLOAD', 'PASTE', 'REPARSE')",
            name="ck_manuscript_versions_source_type",
        ),
        CheckConstraint(
            "parse_status IN ('PENDING', 'SUCCEEDED', 'FAILED')",
            name="ck_manuscript_versions_parse_status",
        ),
        UniqueConstraint(
            "workspace_id", "version_no", name="uq_manuscript_versions_workspace_version"
        ),
        Index(
            "ix_manuscript_versions_workspace_baseline",
            "workspace_id",
            "is_baseline",
        ),
        Index(
            "ix_manuscript_versions_workspace_content_hash",
            "workspace_id",
            "content_hash",
        ),
    )

    manuscript_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    storage_uri: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    parse_status: Mapped[str] = mapped_column(Text, nullable=False)
    structure_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_baseline: Mapped[bool] = mapped_column(Boolean, nullable=False)
