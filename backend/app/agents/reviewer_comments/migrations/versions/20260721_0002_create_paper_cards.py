"""创建 paper_cards 表

Revision ID: 202607210002
Revises: 202607210001
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from langgraph_agent.tools.paper_schemas import CardType, ConfirmationStatus

revision: str = "202607210002"
down_revision: str | None = "202607210001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 ORM / paper_schemas 枚举取值保持一致，不另造常量。
_CARD_TYPE_VALUES = ", ".join(f"'{item.value}'" for item in CardType)
_CONFIRMATION_STATUS_VALUES = ", ".join(
    f"'{item.value}'" for item in ConfirmationStatus
)


def upgrade() -> None:
    """创建 paper_cards 表及其约束和索引。"""
    op.create_table(
        "paper_cards",
        sa.Column(
            "paper_card_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "manuscript_version_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("card_type", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_sections", postgresql.JSONB(), nullable=False),
        sa.Column("source_quote", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("confirmation_status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"card_type IN ({_CARD_TYPE_VALUES})",
            name="ck_paper_cards_card_type",
        ),
        sa.CheckConstraint(
            f"confirmation_status IN ({_CONFIRMATION_STATUS_VALUES})",
            name="ck_paper_cards_confirmation_status",
        ),
        sa.PrimaryKeyConstraint("paper_card_id"),
    )
    op.create_index(
        "ix_paper_cards_workspace_manuscript",
        "paper_cards",
        ["workspace_id", "manuscript_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_paper_cards_workspace_status",
        "paper_cards",
        ["workspace_id", "confirmation_status"],
        unique=False,
    )


def downgrade() -> None:
    """干净删除 paper_cards 表。"""
    op.drop_index("ix_paper_cards_workspace_status", table_name="paper_cards")
    op.drop_index("ix_paper_cards_workspace_manuscript", table_name="paper_cards")
    op.drop_table("paper_cards")
