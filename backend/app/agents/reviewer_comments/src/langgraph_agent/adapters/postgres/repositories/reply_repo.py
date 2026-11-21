"""SourceReply 与 ReplyDraftVersion 数据访问。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from langgraph_agent.adapters.postgres.models import ReplyDraftVersion, SourceReply

from langgraph_agent.adapters.postgres.base import add_and_flush, get_by_pk


class ReplyRepository:
    """来源级回复及其版本化草稿的数据访问。"""

    def create_reply(
        self,
        session: Session,
        *,
        source_id: uuid.UUID,
        suggestion_id: uuid.UUID,
        workspace_id: uuid.UUID,
        strategy: dict[str, Any],
        expression_settings: dict[str, Any],
        response_facts: list[dict[str, Any]] | list[uuid.UUID],
        status: str,
        current_draft_id: uuid.UUID | None,
        input_version: str,
    ) -> SourceReply:
        reply = SourceReply(
            source_id=source_id,
            suggestion_id=suggestion_id,
            workspace_id=workspace_id,
            strategy=strategy,
            expression_settings=expression_settings,
            response_facts=response_facts,
            status=status,
            current_draft_id=current_draft_id,
            input_version=input_version,
        )
        return add_and_flush(session, reply)

    def get_reply(
        self, session: Session, reply_id: uuid.UUID
    ) -> SourceReply | None:
        return get_by_pk(session, SourceReply, reply_id)

    def get_by_source_id(
        self, session: Session, source_id: uuid.UUID
    ) -> SourceReply | None:
        statement = select(SourceReply).where(SourceReply.source_id == source_id)
        return session.scalars(statement).one_or_none()

    def list_by_suggestion(
        self, session: Session, suggestion_id: uuid.UUID
    ) -> list[SourceReply]:
        statement = (
            select(SourceReply)
            .where(SourceReply.suggestion_id == suggestion_id)
            .order_by(SourceReply.created_at, SourceReply.reply_id)
        )
        return list(session.scalars(statement))

    def list_by_workspace(
        self, session: Session, workspace_id: uuid.UUID
    ) -> list[SourceReply]:
        statement = (
            select(SourceReply)
            .where(SourceReply.workspace_id == workspace_id)
            .order_by(SourceReply.created_at, SourceReply.reply_id)
        )
        return list(session.scalars(statement))

    def update_reply_status(
        self,
        session: Session,
        reply_id: uuid.UUID,
        status: str,
    ) -> SourceReply | None:
        reply = self.get_reply(session, reply_id)
        if reply is None:
            return None
        reply.status = status
        session.flush()
        return reply

    def set_current_draft(
        self,
        session: Session,
        reply_id: uuid.UUID,
        draft_id: uuid.UUID | None,
    ) -> SourceReply | None:
        reply = self.get_reply(session, reply_id)
        if reply is None:
            return None
        reply.current_draft_id = draft_id
        session.flush()
        return reply

    def create_draft(
        self,
        session: Session,
        *,
        reply_id: uuid.UUID,
        version_no: int,
        content: str,
        language: str,
        consistency_report: dict[str, Any] | list[Any],
        status: str,
        run_id: uuid.UUID | None,
        approved_at: datetime | None,
        approved_by: str | None,
    ) -> ReplyDraftVersion:
        draft = ReplyDraftVersion(
            reply_id=reply_id,
            version_no=version_no,
            content=content,
            language=language,
            consistency_report=consistency_report,
            status=status,
            run_id=run_id,
            approved_at=approved_at,
            approved_by=approved_by,
        )
        return add_and_flush(session, draft)

    def get_draft(
        self, session: Session, draft_id: uuid.UUID
    ) -> ReplyDraftVersion | None:
        return get_by_pk(session, ReplyDraftVersion, draft_id)

    def get_draft_by_version(
        self,
        session: Session,
        reply_id: uuid.UUID,
        version_no: int,
    ) -> ReplyDraftVersion | None:
        statement = select(ReplyDraftVersion).where(
            ReplyDraftVersion.reply_id == reply_id,
            ReplyDraftVersion.version_no == version_no,
        )
        return session.scalars(statement).one_or_none()

    def get_current_draft(
        self, session: Session, reply_id: uuid.UUID
    ) -> ReplyDraftVersion | None:
        statement = (
            select(ReplyDraftVersion)
            .join(
                SourceReply,
                SourceReply.current_draft_id == ReplyDraftVersion.draft_id,
            )
            .where(SourceReply.reply_id == reply_id)
        )
        return session.scalars(statement).one_or_none()

    def list_drafts(
        self,
        session: Session,
        reply_id: uuid.UUID,
        *,
        status: str | None = None,
    ) -> list[ReplyDraftVersion]:
        statement = select(ReplyDraftVersion).where(
            ReplyDraftVersion.reply_id == reply_id
        )
        if status is not None:
            statement = statement.where(ReplyDraftVersion.status == status)
        statement = statement.order_by(ReplyDraftVersion.version_no)
        return list(session.scalars(statement))

    def update_draft_status(
        self,
        session: Session,
        draft_id: uuid.UUID,
        *,
        status: str,
        approved_at: datetime | None,
        approved_by: str | None,
    ) -> ReplyDraftVersion | None:
        draft = self.get_draft(session, draft_id)
        if draft is None:
            return None
        draft.status = status
        draft.approved_at = approved_at
        draft.approved_by = approved_by
        session.flush()
        return draft
