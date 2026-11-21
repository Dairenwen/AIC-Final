"""GraphRun 数据访问。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from langgraph_agent.adapters.postgres.models import GraphRun

from langgraph_agent.adapters.postgres.base import add_and_flush, get_by_pk


class GraphRunRepository:
    """图运行记录及幂等键查询。"""

    def create(
        self,
        session: Session,
        *,
        workspace_id: uuid.UUID,
        graph_name: str,
        thread_id: str,
        target_type: str,
        target_id: uuid.UUID,
        input_version: str,
        status: str,
        attempt: int,
        error_code: str | None,
        error_message: str | None,
        result_refs: list[dict[str, Any]] | list[uuid.UUID],
        parent_run_id: uuid.UUID | None,
        started_at: datetime | None,
        finished_at: datetime | None,
    ) -> GraphRun:
        graph_run = GraphRun(
            workspace_id=workspace_id,
            graph_name=graph_name,
            thread_id=thread_id,
            target_type=target_type,
            target_id=target_id,
            input_version=input_version,
            status=status,
            attempt=attempt,
            error_code=error_code,
            error_message=error_message,
            result_refs=result_refs,
            parent_run_id=parent_run_id,
            started_at=started_at,
            finished_at=finished_at,
        )
        return add_and_flush(session, graph_run)

    def get_by_id(self, session: Session, run_id: uuid.UUID) -> GraphRun | None:
        return get_by_pk(session, GraphRun, run_id)

    def get_by_idempotency_key(
        self,
        session: Session,
        *,
        graph_name: str,
        target_id: uuid.UUID,
        input_version: str,
        attempt: int,
    ) -> GraphRun | None:
        statement = select(GraphRun).where(
            GraphRun.graph_name == graph_name,
            GraphRun.target_id == target_id,
            GraphRun.input_version == input_version,
            GraphRun.attempt == attempt,
        )
        return session.scalars(statement).one_or_none()

    def list_by_workspace(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        *,
        status: str | None = None,
    ) -> list[GraphRun]:
        statement = select(GraphRun).where(GraphRun.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(GraphRun.status == status)
        statement = statement.order_by(GraphRun.created_at.desc(), GraphRun.run_id)
        return list(session.scalars(statement))

    def list_by_thread(self, session: Session, thread_id: str) -> list[GraphRun]:
        statement = (
            select(GraphRun)
            .where(GraphRun.thread_id == thread_id)
            .order_by(GraphRun.created_at.desc(), GraphRun.run_id)
        )
        return list(session.scalars(statement))

    def get_latest_by_thread(
        self,
        session: Session,
        thread_id: str,
        *,
        input_version: str | None = None,
        for_update: bool = False,
    ) -> GraphRun | None:
        statement = select(GraphRun).where(GraphRun.thread_id == thread_id)
        if input_version is not None:
            statement = statement.where(GraphRun.input_version == input_version)
        statement = statement.order_by(
            GraphRun.created_at.desc(), GraphRun.run_id
        ).limit(1)
        if for_update:
            statement = statement.with_for_update()
        return session.scalars(statement).first()

    def get_active_source_reply_run(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        source_id: uuid.UUID,
    ) -> GraphRun | None:
        statement = (
            select(GraphRun)
            .where(
                GraphRun.workspace_id == workspace_id,
                GraphRun.graph_name == "SourceReplyGraph",
                GraphRun.target_type == "SOURCE",
                GraphRun.target_id == source_id,
                GraphRun.status.in_(("PENDING", "RUNNING", "WAITING_USER")),
            )
            .order_by(GraphRun.created_at.desc(), GraphRun.run_id)
            .limit(1)
        )
        return session.scalars(statement).first()

    def get_latest_source_reply_run(
        self,
        session: Session,
        *,
        workspace_id: uuid.UUID,
        source_id: uuid.UUID,
        input_version: str | None = None,
    ) -> GraphRun | None:
        """返回该来源最新一次 SourceReplyGraph 运行（不限状态）。"""
        statement = select(GraphRun).where(
            GraphRun.workspace_id == workspace_id,
            GraphRun.graph_name == "SourceReplyGraph",
            GraphRun.target_type == "SOURCE",
            GraphRun.target_id == source_id,
        )
        if input_version is not None:
            statement = statement.where(GraphRun.input_version == input_version)
        statement = statement.order_by(
            GraphRun.attempt.desc(),
            GraphRun.created_at.desc(),
            GraphRun.run_id,
        ).limit(1)
        return session.scalars(statement).first()

    def get_active_suggestion_analysis_run(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        suggestion_id: uuid.UUID,
    ) -> GraphRun | None:
        """返回该建议当前进行中的分析 GraphRun（若有）。"""
        statement = (
            select(GraphRun)
            .where(
                GraphRun.workspace_id == workspace_id,
                GraphRun.graph_name == "SuggestionAnalysisGraph",
                GraphRun.target_type == "SUGGESTION",
                GraphRun.target_id == suggestion_id,
                GraphRun.status.in_(("PENDING", "RUNNING", "WAITING_USER")),
            )
            .order_by(GraphRun.created_at.desc(), GraphRun.run_id)
            .limit(1)
        )
        return session.scalars(statement).first()

    def list_active_suggestion_analysis_runs(
        self,
        session: Session,
        workspace_id: uuid.UUID,
    ) -> list[GraphRun]:
        """返回工作区内全部进行中的建议分析 run。"""
        statement = (
            select(GraphRun)
            .where(
                GraphRun.workspace_id == workspace_id,
                GraphRun.graph_name == "SuggestionAnalysisGraph",
                GraphRun.target_type == "SUGGESTION",
                GraphRun.status.in_(("PENDING", "RUNNING", "WAITING_USER")),
            )
            .order_by(GraphRun.created_at.desc(), GraphRun.run_id)
        )
        return list(session.scalars(statement))

    def list_active_source_reply_runs(
        self,
        session: Session,
        workspace_id: uuid.UUID,
    ) -> list[GraphRun]:
        """返回工作区内全部进行中的来源回复 run。"""
        statement = (
            select(GraphRun)
            .where(
                GraphRun.workspace_id == workspace_id,
                GraphRun.graph_name == "SourceReplyGraph",
                GraphRun.target_type == "SOURCE",
                GraphRun.status.in_(("PENDING", "RUNNING", "WAITING_USER")),
            )
            .order_by(GraphRun.created_at.desc(), GraphRun.run_id)
        )
        return list(session.scalars(statement))

    def update_thread_id(
        self,
        session: Session,
        run_id: uuid.UUID,
        thread_id: str,
    ) -> GraphRun | None:
        graph_run = self.get_by_id(session, run_id)
        if graph_run is None:
            return None
        graph_run.thread_id = thread_id
        session.flush()
        return graph_run

    def update_status(
        self,
        session: Session,
        run_id: uuid.UUID,
        *,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        result_refs: list[dict[str, Any]] | list[uuid.UUID] | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> GraphRun | None:
        graph_run = self.get_by_id(session, run_id)
        if graph_run is None:
            return None
        graph_run.status = status
        graph_run.error_code = error_code
        graph_run.error_message = error_message
        if result_refs is not None:
            graph_run.result_refs = result_refs
        if started_at is not None:
            graph_run.started_at = started_at
        # 重新入队 PENDING/RUNNING 时必须清掉旧 finished_at，否则前端会继续显示终态时间戳。
        if status in {"PENDING", "RUNNING"} and finished_at is None:
            graph_run.finished_at = None
        elif finished_at is not None:
            graph_run.finished_at = finished_at
        session.flush()
        return graph_run

    def delete_by_workspace(self, session: Session, workspace_id: uuid.UUID) -> int:
        result = session.execute(
            delete(GraphRun).where(GraphRun.workspace_id == workspace_id)
        )
        return result.rowcount or 0
