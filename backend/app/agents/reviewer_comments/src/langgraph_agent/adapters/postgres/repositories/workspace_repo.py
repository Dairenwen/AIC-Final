"""Workspace 数据访问。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from langgraph_agent.adapters.postgres.models import Workspace

from langgraph_agent.adapters.postgres.base import add_and_flush, get_by_pk


class WorkspaceRepository:
    """工作区的创建、查询、设置更新与软删除。"""

    def create(
        self,
        session: Session,
        *,
        user_id: str,
        title: str,
        mode: str,
        status: str,
        global_settings: dict[str, Any],
        schema_version: int = 1,
    ) -> Workspace:
        workspace = Workspace(
            user_id=user_id,
            title=title,
            mode=mode,
            status=status,
            global_settings=global_settings,
            schema_version=schema_version,
        )
        return add_and_flush(session, workspace)

    def get_by_id(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> Workspace | None:
        if include_deleted:
            return get_by_pk(session, Workspace, workspace_id)

        statement = select(Workspace).where(
            Workspace.workspace_id == workspace_id,
            Workspace.status != "DELETED",
        )
        return session.scalars(statement).one_or_none()

    def list_by_user(
        self,
        session: Session,
        user_id: str,
        *,
        status: str | None = None,
        include_deleted: bool = False,
    ) -> list[Workspace]:
        statement = select(Workspace).where(Workspace.user_id == user_id)
        if not include_deleted:
            statement = statement.where(Workspace.status != "DELETED")
        if status is not None:
            statement = statement.where(Workspace.status == status)
        statement = statement.order_by(
            Workspace.updated_at.desc(), Workspace.workspace_id
        )
        return list(session.scalars(statement))

    def update_status(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        status: str,
        *,
        deleted_at: datetime | None = None,
    ) -> Workspace | None:
        workspace = self.get_by_id(session, workspace_id, include_deleted=True)
        if workspace is None:
            return None
        workspace.status = status
        if deleted_at is not None:
            workspace.deleted_at = deleted_at
        session.flush()
        return workspace

    def update_settings(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        global_settings: dict[str, Any],
    ) -> Workspace | None:
        workspace = self.get_by_id(session, workspace_id, include_deleted=True)
        if workspace is None:
            return None
        workspace.global_settings = global_settings
        session.flush()
        return workspace

    def soft_delete(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        *,
        deleted_at: datetime | None = None,
    ) -> Workspace | None:
        return self.update_status(
            session,
            workspace_id,
            "DELETED",
            deleted_at=deleted_at or datetime.now(timezone.utc),
        )
