"""增加来源级表达设置覆盖

Revision ID: 202607230003
Revises: 202607210002
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "202607230003"
down_revision: str | None = "202607210002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "suggestion_sources",
        sa.Column(
            "expression_settings_override",
            postgresql.JSONB(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("suggestion_sources", "expression_settings_override")
