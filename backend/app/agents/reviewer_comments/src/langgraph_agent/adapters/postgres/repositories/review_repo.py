"""ReviewParty 与 ReviewInput 数据访问。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from langgraph_agent.adapters.postgres.models import ReviewInput, ReviewParty

from langgraph_agent.adapters.postgres.base import add_and_flush, get_by_pk


class ReviewRepository:
    """审稿方身份及版本化原始审稿材料的数据访问。"""

    def create_party(
        self,
        session: Session,
        *,
        workspace_id: uuid.UUID,
        role: str,
        display_name: str,
        raw_label: str,
        created_at: datetime | None = None,
    ) -> ReviewParty:
        party = ReviewParty(
            workspace_id=workspace_id,
            role=role,
            display_name=display_name,
            raw_label=raw_label,
            created_at=created_at,
        )
        return add_and_flush(session, party)

    def get_party(self, session: Session, party_id: uuid.UUID) -> ReviewParty | None:
        return get_by_pk(session, ReviewParty, party_id)

    def find_party(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        *,
        role: str,
        display_name: str,
    ) -> ReviewParty | None:
        statement = select(ReviewParty).where(
            ReviewParty.workspace_id == workspace_id,
            ReviewParty.role == role,
            ReviewParty.display_name == display_name,
        )
        return session.scalars(statement).one_or_none()

    def list_parties(
        self, session: Session, workspace_id: uuid.UUID
    ) -> list[ReviewParty]:
        statement = (
            select(ReviewParty)
            .where(ReviewParty.workspace_id == workspace_id)
            .order_by(ReviewParty.created_at, ReviewParty.party_id)
        )
        return list(session.scalars(statement))

    def create_input(
        self,
        session: Session,
        *,
        workspace_id: uuid.UUID,
        party_id: uuid.UUID,
        version_no: int,
        raw_text: str | None,
        storage_uri: str | None,
        content_hash: str,
        language: str | None,
        is_current: bool,
    ) -> ReviewInput:
        review_input = ReviewInput(
            workspace_id=workspace_id,
            party_id=party_id,
            version_no=version_no,
            raw_text=raw_text,
            storage_uri=storage_uri,
            content_hash=content_hash,
            language=language,
            is_current=is_current,
        )
        return add_and_flush(session, review_input)

    def get_input(
        self, session: Session, review_input_id: uuid.UUID
    ) -> ReviewInput | None:
        return get_by_pk(session, ReviewInput, review_input_id)

    def get_input_by_version(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        party_id: uuid.UUID,
        version_no: int,
    ) -> ReviewInput | None:
        statement = select(ReviewInput).where(
            ReviewInput.workspace_id == workspace_id,
            ReviewInput.party_id == party_id,
            ReviewInput.version_no == version_no,
        )
        return session.scalars(statement).one_or_none()

    def get_current_input(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        party_id: uuid.UUID,
    ) -> ReviewInput | None:
        statement = (
            select(ReviewInput)
            .where(
                ReviewInput.workspace_id == workspace_id,
                ReviewInput.party_id == party_id,
                ReviewInput.is_current.is_(True),
            )
            .order_by(ReviewInput.version_no.desc())
            .limit(1)
        )
        return session.scalars(statement).first()

    def list_inputs(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        *,
        party_id: uuid.UUID | None = None,
        current_only: bool = False,
    ) -> list[ReviewInput]:
        statement = (
            select(ReviewInput)
            .join(ReviewParty, ReviewInput.party_id == ReviewParty.party_id)
            .where(ReviewInput.workspace_id == workspace_id)
        )
        if party_id is not None:
            statement = statement.where(ReviewInput.party_id == party_id)
        if current_only:
            statement = statement.where(ReviewInput.is_current.is_(True))
        statement = statement.order_by(
            ReviewParty.created_at,
            ReviewParty.party_id,
            ReviewInput.version_no,
        )
        return list(session.scalars(statement))

    def set_current(
        self,
        session: Session,
        review_input_id: uuid.UUID,
        *,
        is_current: bool,
    ) -> ReviewInput | None:
        review_input = self.get_input(session, review_input_id)
        if review_input is None:
            return None
        review_input.is_current = is_current
        session.flush()
        return review_input

    def delete_inputs_by_workspace(
        self, session: Session, workspace_id: uuid.UUID
    ) -> int:
        result = session.execute(
            delete(ReviewInput).where(ReviewInput.workspace_id == workspace_id)
        )
        return result.rowcount or 0

    def delete_parties_by_workspace(
        self, session: Session, workspace_id: uuid.UUID
    ) -> int:
        result = session.execute(
            delete(ReviewParty).where(ReviewParty.workspace_id == workspace_id)
        )
        return result.rowcount or 0
