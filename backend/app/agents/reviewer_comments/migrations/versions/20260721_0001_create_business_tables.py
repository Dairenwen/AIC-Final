"""创建业务数据表

Revision ID: 202607210001
Revises:
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "202607210001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 12 张业务表及其约束和索引。"""

    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "workspaces",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("global_settings", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "schema_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.CheckConstraint("mode IN ('FAST', 'SLOW')", name="ck_workspaces_mode"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'ARCHIVED', 'DELETED')",
            name="ck_workspaces_status",
        ),
        sa.PrimaryKeyConstraint("workspace_id"),
    )
    op.create_index(
        "ix_workspaces_user_id_status",
        "workspaces",
        ["user_id", "status"],
        unique=False,
    )

    op.create_table(
        "manuscript_versions",
        sa.Column(
            "manuscript_version_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("storage_uri", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("parse_status", sa.Text(), nullable=False),
        sa.Column("structure_summary", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("is_baseline", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('UPLOAD', 'PASTE', 'REPARSE')",
            name="ck_manuscript_versions_source_type",
        ),
        sa.CheckConstraint(
            "parse_status IN ('PENDING', 'SUCCEEDED', 'FAILED')",
            name="ck_manuscript_versions_parse_status",
        ),
        sa.PrimaryKeyConstraint("manuscript_version_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "version_no",
            name="uq_manuscript_versions_workspace_version",
        ),
    )
    op.create_index(
        "ix_manuscript_versions_workspace_baseline",
        "manuscript_versions",
        ["workspace_id", "is_baseline"],
        unique=False,
    )
    op.create_index(
        "ix_manuscript_versions_workspace_content_hash",
        "manuscript_versions",
        ["workspace_id", "content_hash"],
        unique=False,
    )

    op.create_table(
        "review_parties",
        sa.Column(
            "party_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("raw_label", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('EDITOR', 'REVIEWER', 'UNKNOWN')",
            name="ck_review_parties_role",
        ),
        sa.PrimaryKeyConstraint("party_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "role",
            "display_name",
            name="uq_review_parties_workspace_role_name",
        ),
    )

    op.create_table(
        "suggestions",
        sa.Column(
            "suggestion_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_text", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("merge_group_key", sa.String(), nullable=True),
        sa.Column("conflict_group_key", sa.String(), nullable=True),
        sa.Column("priority", sa.Text(), nullable=True),
        sa.Column("category_ids", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("input_version", sa.String(), nullable=False),
        sa.Column(
            "current_analysis_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'WAITING_USER', 'READY_FOR_REPLY', "
            "'SUCCEEDED', 'FAILED_RETRYABLE', 'FAILED_FINAL', 'STALE', "
            "'SUPERSEDED', 'CANCELLED')",
            name="ck_suggestions_status",
        ),
        sa.CheckConstraint(
            "priority IS NULL OR priority IN ('P0', 'P1', 'P2', 'P3')",
            name="ck_suggestions_priority",
        ),
        sa.PrimaryKeyConstraint("suggestion_id"),
    )
    op.create_index(
        "ix_suggestions_workspace_merge_group",
        "suggestions",
        ["workspace_id", "merge_group_key"],
        unique=False,
    )
    op.create_index(
        "ix_suggestions_workspace_status",
        "suggestions",
        ["workspace_id", "status"],
        unique=False,
    )

    op.create_table(
        "decision_events",
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "target_type IN ('SUGGESTION', 'SOURCE', 'ANALYSIS', 'FACT', "
            "'REPLY', 'DRAFT', 'EXPORT')",
            name="ck_decision_events_target_type",
        ),
        sa.CheckConstraint(
            "action IN ('CONFIRM', 'REJECT', 'EDIT', 'REOPEN', 'SUPERSEDE', "
            "'CANCEL')",
            name="ck_decision_events_action",
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_decision_events_target",
        "decision_events",
        ["target_type", "target_id"],
        unique=False,
    )
    op.create_index(
        "ix_decision_events_workspace_created",
        "decision_events",
        ["workspace_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "graph_runs",
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("graph_name", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_version", sa.String(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_refs", postgresql.JSONB(), nullable=False),
        sa.Column("parent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "graph_name IN ('WorkspaceTaskGraph', 'SuggestionAnalysisGraph', "
            "'SourceReplyGraph')",
            name="ck_graph_runs_graph_name",
        ),
        sa.CheckConstraint(
            "target_type IN ('WORKSPACE', 'SUGGESTION', 'SOURCE')",
            name="ck_graph_runs_target_type",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'WAITING_USER', 'SUCCEEDED', "
            "'FAILED_RETRYABLE', 'FAILED_FINAL', 'SUPERSEDED', 'CANCELLED')",
            name="ck_graph_runs_status",
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(
        "ix_graph_runs_thread_id", "graph_runs", ["thread_id"], unique=False
    )
    op.create_index(
        "ix_graph_runs_workspace_status",
        "graph_runs",
        ["workspace_id", "status"],
        unique=False,
    )
    op.create_index(
        "uq_graph_runs_idempotency",
        "graph_runs",
        ["graph_name", "target_id", "input_version", "attempt"],
        unique=True,
    )

    op.create_table(
        "review_inputs",
        sa.Column(
            "review_input_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("storage_uri", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["party_id"], ["review_parties.party_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("review_input_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "party_id",
            "version_no",
            name="uq_review_inputs_workspace_party_version",
        ),
    )

    op.create_table(
        "suggestion_sources",
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("suggestion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_input_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("localized_claim", sa.Text(), nullable=False),
        sa.Column("stance", sa.Text(), nullable=True),
        sa.Column("span_refs", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stance IS NULL OR stance IN ('REQUEST', 'CONCERN', 'PRAISE', 'ADMIN')",
            name="ck_suggestion_sources_stance",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'SUPERSEDED', 'IGNORED')",
            name="ck_suggestion_sources_status",
        ),
        sa.ForeignKeyConstraint(
            ["suggestion_id"], ["suggestions.suggestion_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("source_id"),
        sa.UniqueConstraint(
            "suggestion_id",
            "party_id",
            "content_hash",
            name="uq_suggestion_sources_suggestion_party_hash",
        ),
    )
    op.create_index(
        "ix_suggestion_sources_suggestion_id",
        "suggestion_sources",
        ["suggestion_id"],
        unique=False,
    )

    op.create_table(
        "analysis_snapshots",
        sa.Column(
            "analysis_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("suggestion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_version", sa.String(), nullable=False),
        sa.Column("categories", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_items", postgresql.JSONB(), nullable=False),
        sa.Column("coverage", sa.Text(), nullable=False),
        sa.Column("priority", sa.Text(), nullable=False),
        sa.Column("recommended_actions", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "coverage IN ('FULL', 'PARTIAL', 'NONE', 'UNKNOWN')",
            name="ck_analysis_snapshots_coverage",
        ),
        sa.CheckConstraint(
            "priority IN ('P0', 'P1', 'P2', 'P3')",
            name="ck_analysis_snapshots_priority",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'CONFIRMED', 'STALE', 'SUPERSEDED')",
            name="ck_analysis_snapshots_status",
        ),
        sa.ForeignKeyConstraint(
            ["suggestion_id"], ["suggestions.suggestion_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("analysis_id"),
    )
    op.create_index(
        "ix_analysis_snapshots_suggestion_status",
        "analysis_snapshots",
        ["suggestion_id", "status"],
        unique=False,
    )
    op.create_index(
        "uq_analysis_snapshots_run_id",
        "analysis_snapshots",
        ["run_id"],
        unique=True,
    )

    op.create_table(
        "modification_facts",
        sa.Column(
            "fact_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("suggestion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("paper_change_summary", sa.Text(), nullable=False),
        sa.Column("response_fact_summary", sa.Text(), nullable=False),
        sa.Column("constraints", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("input_version", sa.String(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action_type IN ('ACCEPT', 'PARTIAL_ACCEPT', 'REJECT', 'CLARIFY', "
            "'DEFER')",
            name="ck_modification_facts_action_type",
        ),
        sa.CheckConstraint(
            "status IN ('CONFIRMED', 'STALE', 'SUPERSEDED')",
            name="ck_modification_facts_status",
        ),
        sa.ForeignKeyConstraint(
            ["suggestion_id"], ["suggestions.suggestion_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("fact_id"),
    )
    op.create_index(
        "ix_modification_facts_suggestion_status",
        "modification_facts",
        ["suggestion_id", "status"],
        unique=False,
    )

    op.create_table(
        "source_replies",
        sa.Column(
            "reply_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suggestion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy", postgresql.JSONB(), nullable=False),
        sa.Column("expression_settings", postgresql.JSONB(), nullable=False),
        sa.Column("response_facts", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_draft_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_version", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'STRATEGY_WAITING', 'FACTS_WAITING', "
            "'DRAFTING', 'REVIEW_WAITING', 'APPROVED', 'FAILED_RETRYABLE', "
            "'FAILED_FINAL', 'STALE', 'SUPERSEDED', 'CANCELLED')",
            name="ck_source_replies_status",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["suggestion_sources.source_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("reply_id"),
        sa.UniqueConstraint("source_id", name="uq_source_replies_source_id"),
    )
    op.create_index(
        "ix_source_replies_suggestion_id",
        "source_replies",
        ["suggestion_id"],
        unique=False,
    )

    op.create_table(
        "reply_draft_versions",
        sa.Column(
            "draft_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("reply_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("language", sa.String(), nullable=False),
        sa.Column("consistency_report", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(), nullable=True),
        sa.CheckConstraint(
            "status IN ('GENERATED', 'EDITED', 'APPROVED', 'REJECTED', 'STALE')",
            name="ck_reply_draft_versions_status",
        ),
        sa.ForeignKeyConstraint(
            ["reply_id"], ["source_replies.reply_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("draft_id"),
        sa.UniqueConstraint(
            "reply_id",
            "version_no",
            name="uq_reply_draft_versions_reply_version",
        ),
    )
    op.create_index(
        "ix_reply_draft_versions_reply_status",
        "reply_draft_versions",
        ["reply_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    """按依赖关系逆序删除全部业务表。"""

    op.drop_index(
        "ix_reply_draft_versions_reply_status",
        table_name="reply_draft_versions",
    )
    op.drop_table("reply_draft_versions")
    op.drop_index("ix_source_replies_suggestion_id", table_name="source_replies")
    op.drop_table("source_replies")
    op.drop_index(
        "ix_modification_facts_suggestion_status",
        table_name="modification_facts",
    )
    op.drop_table("modification_facts")
    op.drop_index("uq_analysis_snapshots_run_id", table_name="analysis_snapshots")
    op.drop_index(
        "ix_analysis_snapshots_suggestion_status",
        table_name="analysis_snapshots",
    )
    op.drop_table("analysis_snapshots")
    op.drop_index(
        "ix_suggestion_sources_suggestion_id", table_name="suggestion_sources"
    )
    op.drop_table("suggestion_sources")
    op.drop_table("review_inputs")
    op.drop_index("uq_graph_runs_idempotency", table_name="graph_runs")
    op.drop_index("ix_graph_runs_workspace_status", table_name="graph_runs")
    op.drop_index("ix_graph_runs_thread_id", table_name="graph_runs")
    op.drop_table("graph_runs")
    op.drop_index(
        "ix_decision_events_workspace_created", table_name="decision_events"
    )
    op.drop_index("ix_decision_events_target", table_name="decision_events")
    op.drop_table("decision_events")
    op.drop_index("ix_suggestions_workspace_status", table_name="suggestions")
    op.drop_index("ix_suggestions_workspace_merge_group", table_name="suggestions")
    op.drop_table("suggestions")
    op.drop_table("review_parties")
    op.drop_index(
        "ix_manuscript_versions_workspace_content_hash",
        table_name="manuscript_versions",
    )
    op.drop_index(
        "ix_manuscript_versions_workspace_baseline",
        table_name="manuscript_versions",
    )
    op.drop_table("manuscript_versions")
    op.drop_index("ix_workspaces_user_id_status", table_name="workspaces")
    op.drop_table("workspaces")
