from typing import Any

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Role(TimestampMixin, db.Model):
    __tablename__ = "roles"
    __table_args__ = {"schema": "zhiyan"}

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)


class User(UUIDPrimaryKeyMixin, TimestampMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            r"phone ~ '^\+?[1-9][0-9]{7,14}$'",
            name="ck_users_phone_format",
        ),
        {"schema": "zhiyan"},
    )

    phone: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role_code: Mapped[str] = mapped_column(
        ForeignKey("zhiyan.roles.code"), default="normal_user", nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    avatar_object_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    session_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
