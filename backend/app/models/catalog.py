from typing import Any
from uuid import UUID

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ModelProvider(TimestampMixin, db.Model):
    __tablename__ = "model_providers"
    __table_args__ = {"schema": "zhiyan"}

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    default_base_url: Mapped[str | None] = mapped_column(Text)
    allow_custom_url: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    capabilities: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    config_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    privacy_policy_url: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)


class ModelType(TimestampMixin, db.Model):
    __tablename__ = "model_types"
    __table_args__ = {"schema": "zhiyan"}

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)


class ModelConfig(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "model_configs"
    __table_args__ = {"schema": "zhiyan"}

    config_scope: Mapped[str] = mapped_column(String(20), nullable=False)
    owner_user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    is_account_api: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provider_code: Mapped[str] = mapped_column(
        ForeignKey("zhiyan.model_providers.code"), nullable=False
    )
    model_type_code: Mapped[str] = mapped_column(
        ForeignKey("zhiyan.model_types.code"), default="chat", nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    capabilities: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    default_for: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    encrypted_api_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[str | None] = mapped_column(String(64))
    key_last_four: Mapped[str | None] = mapped_column(String(8))
    allow_platform_fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    external_processing_acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Agent(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "agents"
    __table_args__ = {"schema": "zhiyan"}

    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))


class AgentTeam(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "agent_teams"
    __table_args__ = {"schema": "zhiyan"}

    owner_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zhiyan.users.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(String(16), default="PRIVATE", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    team_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)


class Tool(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "tools"
    __table_args__ = {"schema": "zhiyan"}

    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    risk_level: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))


class Skill(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "skills"
    __table_args__ = {"schema": "zhiyan"}

    owner_user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(String(16), default="PRIVATE", nullable=False)
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    review_status: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    reviewer_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    review_comment: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
