"""AnalysisSnapshot 与 ModificationFact 数据访问。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from langgraph_agent.adapters.postgres.models import AnalysisSnapshot, ModificationFact, Suggestion

from langgraph_agent.adapters.postgres.base import add_and_flush, get_by_pk


class AnalysisRepository:
    """建议级分析快照与修改事实的数据访问。"""

    def create_snapshot(
        self,
        session: Session,
        *,
        suggestion_id: uuid.UUID,
        workspace_id: uuid.UUID,
        run_id: uuid.UUID,
        input_version: str,
        categories: dict[str, Any] | list[Any],
        evidence_items: list[dict[str, Any]],
        coverage: str,
        priority: str,
        recommended_actions: list[dict[str, Any]] | dict[str, Any],
        confidence: float | None,
        status: str,
        confirmed_at: datetime | None,
        confirmed_by: str | None,
    ) -> AnalysisSnapshot:
        snapshot = AnalysisSnapshot(
            suggestion_id=suggestion_id,
            workspace_id=workspace_id,
            run_id=run_id,
            input_version=input_version,
            categories=categories,
            evidence_items=evidence_items,
            coverage=coverage,
            priority=priority,
            recommended_actions=recommended_actions,
            confidence=confidence,
            status=status,
            confirmed_at=confirmed_at,
            confirmed_by=confirmed_by,
        )
        return add_and_flush(session, snapshot)

    def get_snapshot(
        self, session: Session, analysis_id: uuid.UUID
    ) -> AnalysisSnapshot | None:
        return get_by_pk(session, AnalysisSnapshot, analysis_id)

    def get_by_run_id(
        self, session: Session, run_id: uuid.UUID
    ) -> AnalysisSnapshot | None:
        statement = select(AnalysisSnapshot).where(
            AnalysisSnapshot.run_id == run_id
        )
        return session.scalars(statement).one_or_none()

    def get_current_snapshot(
        self, session: Session, suggestion_id: uuid.UUID
    ) -> AnalysisSnapshot | None:
        statement = (
            select(AnalysisSnapshot)
            .join(
                Suggestion,
                Suggestion.current_analysis_id == AnalysisSnapshot.analysis_id,
            )
            .where(Suggestion.suggestion_id == suggestion_id)
        )
        return session.scalars(statement).one_or_none()

    def list_snapshots(
        self,
        session: Session,
        suggestion_id: uuid.UUID,
        *,
        status: str | None = None,
    ) -> list[AnalysisSnapshot]:
        statement = select(AnalysisSnapshot).where(
            AnalysisSnapshot.suggestion_id == suggestion_id
        )
        if status is not None:
            statement = statement.where(AnalysisSnapshot.status == status)
        statement = statement.order_by(
            AnalysisSnapshot.created_at, AnalysisSnapshot.analysis_id
        )
        return list(session.scalars(statement))

    def update_snapshot_status(
        self,
        session: Session,
        analysis_id: uuid.UUID,
        *,
        status: str,
        confirmed_at: datetime | None,
        confirmed_by: str | None,
    ) -> AnalysisSnapshot | None:
        snapshot = self.get_snapshot(session, analysis_id)
        if snapshot is None:
            return None
        snapshot.status = status
        snapshot.confirmed_at = confirmed_at
        snapshot.confirmed_by = confirmed_by
        session.flush()
        return snapshot

    def create_fact(
        self,
        session: Session,
        *,
        suggestion_id: uuid.UUID,
        workspace_id: uuid.UUID,
        action_type: str,
        paper_change_summary: str,
        response_fact_summary: str,
        constraints: dict[str, Any] | list[Any],
        status: str,
        input_version: str,
        confirmed_at: datetime | None,
        confirmed_by: str | None,
    ) -> ModificationFact:
        fact = ModificationFact(
            suggestion_id=suggestion_id,
            workspace_id=workspace_id,
            action_type=action_type,
            paper_change_summary=paper_change_summary,
            response_fact_summary=response_fact_summary,
            constraints=constraints,
            status=status,
            input_version=input_version,
            confirmed_at=confirmed_at,
            confirmed_by=confirmed_by,
        )
        return add_and_flush(session, fact)

    def get_fact(
        self, session: Session, fact_id: uuid.UUID
    ) -> ModificationFact | None:
        return get_by_pk(session, ModificationFact, fact_id)

    def list_facts(
        self,
        session: Session,
        suggestion_id: uuid.UUID,
        *,
        status: str | None = None,
    ) -> list[ModificationFact]:
        statement = select(ModificationFact).where(
            ModificationFact.suggestion_id == suggestion_id
        )
        if status is not None:
            statement = statement.where(ModificationFact.status == status)
        statement = statement.order_by(
            ModificationFact.created_at, ModificationFact.fact_id
        )
        return list(session.scalars(statement))

    def update_fact_status(
        self,
        session: Session,
        fact_id: uuid.UUID,
        status: str,
    ) -> ModificationFact | None:
        fact = self.get_fact(session, fact_id)
        if fact is None:
            return None
        fact.status = status
        session.flush()
        return fact
