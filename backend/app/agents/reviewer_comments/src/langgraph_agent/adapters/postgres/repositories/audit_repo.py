"""DecisionEvent 数据访问。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from langgraph_agent.adapters.postgres.models import DecisionEvent

from langgraph_agent.adapters.postgres.base import add_and_flush, get_by_pk


class AuditRepository:
    """人工决策审计事件的追加与查询。"""

    def create(
        self,
        session: Session,
        *,
        workspace_id: uuid.UUID,
        target_type: str,
        target_id: uuid.UUID,
        action: str,
        payload: dict[str, Any],
        actor_user_id: str,
    ) -> DecisionEvent:
        event = DecisionEvent(
            workspace_id=workspace_id,
            target_type=target_type,
            target_id=target_id,
            action=action,
            payload=payload,
            actor_user_id=actor_user_id,
        )
        return add_and_flush(session, event)

    def get_by_id(
        self, session: Session, event_id: uuid.UUID
    ) -> DecisionEvent | None:
        return get_by_pk(session, DecisionEvent, event_id)

    def list_by_workspace(
        self, session: Session, workspace_id: uuid.UUID
    ) -> list[DecisionEvent]:
        statement = (
            select(DecisionEvent)
            .where(DecisionEvent.workspace_id == workspace_id)
            .order_by(DecisionEvent.created_at, DecisionEvent.event_id)
        )
        return list(session.scalars(statement))

    def list_by_target(
        self,
        session: Session,
        *,
        target_type: str,
        target_id: uuid.UUID,
    ) -> list[DecisionEvent]:
        statement = (
            select(DecisionEvent)
            .where(
                DecisionEvent.target_type == target_type,
                DecisionEvent.target_id == target_id,
            )
            .order_by(DecisionEvent.created_at, DecisionEvent.event_id)
        )
        return list(session.scalars(statement))
