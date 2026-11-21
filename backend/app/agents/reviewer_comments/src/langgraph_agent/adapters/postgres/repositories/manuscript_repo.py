"""ManuscriptVersion 数据访问。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from langgraph_agent.adapters.postgres.models import ManuscriptVersion

from langgraph_agent.adapters.postgres.base import add_and_flush, get_by_pk


class ManuscriptRepository:
    """论文版本的创建、版本查询与状态更新。"""

    def create(
        self,
        session: Session,
        *,
        workspace_id: uuid.UUID,
        version_no: int,
        source_type: str,
        storage_uri: str,
        content_hash: str,
        parse_status: str,
        structure_summary: dict[str, Any],
        is_baseline: bool,
    ) -> ManuscriptVersion:
        manuscript = ManuscriptVersion(
            workspace_id=workspace_id,
            version_no=version_no,
            source_type=source_type,
            storage_uri=storage_uri,
            content_hash=content_hash,
            parse_status=parse_status,
            structure_summary=structure_summary,
            is_baseline=is_baseline,
        )
        return add_and_flush(session, manuscript)

    def get_by_id(
        self, session: Session, manuscript_version_id: uuid.UUID
    ) -> ManuscriptVersion | None:
        return get_by_pk(session, ManuscriptVersion, manuscript_version_id)

    def get_by_version(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        version_no: int,
    ) -> ManuscriptVersion | None:
        statement = select(ManuscriptVersion).where(
            ManuscriptVersion.workspace_id == workspace_id,
            ManuscriptVersion.version_no == version_no,
        )
        return session.scalars(statement).one_or_none()

    def list_by_workspace(
        self, session: Session, workspace_id: uuid.UUID
    ) -> list[ManuscriptVersion]:
        statement = (
            select(ManuscriptVersion)
            .where(ManuscriptVersion.workspace_id == workspace_id)
            .order_by(ManuscriptVersion.version_no)
        )
        return list(session.scalars(statement))

    def list_pending(self, session: Session) -> list[ManuscriptVersion]:
        """返回可在应用启动恢复的 PENDING 版本。"""
        statement = (
            select(ManuscriptVersion)
            .where(ManuscriptVersion.parse_status == "PENDING")
            .order_by(ManuscriptVersion.created_at, ManuscriptVersion.manuscript_version_id)
        )
        return list(session.scalars(statement))

    def claim_pending(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        manuscript_version_id: uuid.UUID,
        *,
        structure_summary: dict[str, Any],
    ) -> ManuscriptVersion | None:
        """以短事务锁定并标记仍为 PENDING 的版本。"""
        statement = (
            select(ManuscriptVersion)
            .where(
                ManuscriptVersion.workspace_id == workspace_id,
                ManuscriptVersion.manuscript_version_id == manuscript_version_id,
                ManuscriptVersion.parse_status == "PENDING",
            )
            .with_for_update()
        )
        manuscript = session.scalars(statement).one_or_none()
        if manuscript is None:
            return None
        manuscript.structure_summary = structure_summary
        session.flush()
        return manuscript

    def get_baseline(
        self, session: Session, workspace_id: uuid.UUID
    ) -> ManuscriptVersion | None:
        statement = (
            select(ManuscriptVersion)
            .where(
                ManuscriptVersion.workspace_id == workspace_id,
                ManuscriptVersion.is_baseline.is_(True),
            )
            .order_by(ManuscriptVersion.version_no.desc())
            .limit(1)
        )
        return session.scalars(statement).first()

    def get_by_content_hash(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        content_hash: str,
    ) -> ManuscriptVersion | None:
        statement = select(ManuscriptVersion).where(
            ManuscriptVersion.workspace_id == workspace_id,
            ManuscriptVersion.content_hash == content_hash,
        )
        return session.scalars(statement).first()

    def update_parse_result(
        self,
        session: Session,
        manuscript_version_id: uuid.UUID,
        *,
        parse_status: str,
        structure_summary: dict[str, Any],
        expected_status: str | None = None,
    ) -> ManuscriptVersion | None:
        manuscript = self.get_by_id(session, manuscript_version_id)
        if manuscript is None:
            return None
        if expected_status is not None and manuscript.parse_status != expected_status:
            return None
        manuscript.parse_status = parse_status
        manuscript.structure_summary = structure_summary
        session.flush()
        return manuscript

    def set_baseline(
        self,
        session: Session,
        manuscript_version_id: uuid.UUID,
        *,
        is_baseline: bool,
    ) -> ManuscriptVersion | None:
        manuscript = self.get_by_id(session, manuscript_version_id)
        if manuscript is None:
            return None
        if is_baseline:
            if manuscript.parse_status != "SUCCEEDED":
                raise ValueError("只有解析成功的论文版本才能设置 baseline")
            session.execute(
                update(ManuscriptVersion)
                .where(
                    ManuscriptVersion.workspace_id == manuscript.workspace_id,
                    ManuscriptVersion.manuscript_version_id
                    != manuscript_version_id,
                )
                .values(is_baseline=False)
            )
        manuscript.is_baseline = is_baseline
        session.flush()
        return manuscript

    def next_version_no(self, session: Session, workspace_id: uuid.UUID) -> int:
        """返回该 workspace 下下一个 version_no：max(version_no)+1，无则 1。"""
        statement = select(func.max(ManuscriptVersion.version_no)).where(
            ManuscriptVersion.workspace_id == workspace_id
        )
        current_max = session.scalar(statement)
        if current_max is None:
            return 1
        return int(current_max) + 1

    def delete_by_workspace(self, session: Session, workspace_id: uuid.UUID) -> int:
        result = session.execute(
            delete(ManuscriptVersion).where(
                ManuscriptVersion.workspace_id == workspace_id
            )
        )
        return result.rowcount or 0
