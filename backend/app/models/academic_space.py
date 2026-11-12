from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .mixins import TimestampMixin, UUIDPrimaryKeyMixin


class PersonalKnowledgeFolder(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "personal_knowledge_folders"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "name", name="uq_personal_kb_folder_owner_name"),
        {"schema": "zhiyan"},
    )

    owner_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zhiyan.users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_folder_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zhiyan.personal_knowledge_folders.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str] = mapped_column(String(16), default="#47745b", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)


class PersonalKnowledgePaper(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "personal_knowledge_papers"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "folder_id",
            "platform_paper_id",
            name="uq_personal_kb_paper_platform_folder",
        ),
        {"schema": "zhiyan"},
    )

    owner_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zhiyan.users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    folder_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("zhiyan.personal_knowledge_folders.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    platform_paper_id: Mapped[str | None] = mapped_column(Text, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    abstract: Mapped[str | None] = mapped_column(Text)
    publish_venue: Mapped[str | None] = mapped_column(Text)
    publish_year: Mapped[int | None]
    source_url: Mapped[str | None] = mapped_column(Text)
    object_key: Mapped[str | None] = mapped_column(Text)
    original_file_name: Mapped[str | None] = mapped_column(Text)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
