"""审稿方与原始审稿材料数据模型。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from langgraph_agent.adapters.postgres.models import Base


class ReviewParty(Base):
    """编辑或审稿人的工作区内身份。"""

    __tablename__ = "review_parties"
    __table_args__ = (
        CheckConstraint(
            "role IN ('EDITOR', 'REVIEWER', 'UNKNOWN')",
            name="ck_review_parties_role",
        ),
        UniqueConstraint(
            "workspace_id",
            "role",
            "display_name",
            name="uq_review_parties_workspace_role_name",
        ),
    )

    party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    raw_label: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReviewInput(Base):
    """版本化保存的原始审稿材料。"""

    __tablename__ = "review_inputs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "party_id",
            "version_no",
            name="uq_review_inputs_workspace_party_version",
        ),
    )

    review_input_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("review_parties.party_id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    storage_uri: Mapped[str | None] = mapped_column(String)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    language: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
