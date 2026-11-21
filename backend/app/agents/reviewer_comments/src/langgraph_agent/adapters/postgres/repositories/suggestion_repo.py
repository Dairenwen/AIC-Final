"""Suggestion 与 SuggestionSource 数据访问。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from langgraph_agent.adapters.postgres.base import add_and_flush, get_by_pk
from langgraph_agent.adapters.postgres.models import Suggestion, SuggestionSource, Workspace


def default_response_settings() -> dict[str, Any]:
    """系统默认表达设置（对齐 backend workspace_service._default_settings）。"""
    from langgraph_agent.schemas.workspace import ResponseSettings, SettingsSource

    return ResponseSettings(
        response_language="中文",
        tone="正式、礼貌",
        author_reference="The authors",
        target_length="标准",
        terminology_preferences=[],
        source=SettingsSource.SYSTEM_DEFAULT,
    ).model_dump(mode="json")


def get_effective_response_settings(
    workspace: Workspace,
    source: SuggestionSource,
) -> dict[str, Any]:
    """返回来源当前实际使用的完整表达设置。

    优先 source 覆盖，否则 workspace.global_settings；
    为空或校验失败时回退系统默认（避免 seed/旧数据 global_settings={} 导致 REPLY 崩溃）。
    """
    if source.workspace_id != workspace.workspace_id:
        raise ValueError("SuggestionSource 不属于指定 Workspace")
    stored = (
        source.expression_settings_override
        if source.expression_settings_override is not None
        else workspace.global_settings
    )
    if not isinstance(stored, dict) or not stored:
        return default_response_settings()
    try:
        from langgraph_agent.schemas.workspace import ResponseSettings

        return ResponseSettings.model_validate(stored).model_dump(mode="json")
    except Exception:
        return default_response_settings()


class SuggestionRepository:
    """共享建议及其审稿来源的数据访问。"""

    def create_suggestion(
        self,
        session: Session,
        *,
        workspace_id: uuid.UUID,
        canonical_text: str,
        status: str,
        merge_group_key: str | None,
        conflict_group_key: str | None,
        priority: str | None,
        category_ids: list[str],
        input_version: str,
        current_analysis_id: uuid.UUID | None,
    ) -> Suggestion:
        suggestion = Suggestion(
            workspace_id=workspace_id,
            canonical_text=canonical_text,
            status=status,
            merge_group_key=merge_group_key,
            conflict_group_key=conflict_group_key,
            priority=priority,
            category_ids=category_ids,
            input_version=input_version,
            current_analysis_id=current_analysis_id,
        )
        return add_and_flush(session, suggestion)

    def get_suggestion(
        self, session: Session, suggestion_id: uuid.UUID
    ) -> Suggestion | None:
        return get_by_pk(session, Suggestion, suggestion_id)

    def list_suggestions(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        *,
        status: str | None = None,
    ) -> list[Suggestion]:
        statement = select(Suggestion).where(
            Suggestion.workspace_id == workspace_id
        )
        if status is not None:
            statement = statement.where(Suggestion.status == status)
        statement = statement.order_by(Suggestion.created_at, Suggestion.suggestion_id)
        return list(session.scalars(statement))

    def find_by_merge_group(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        merge_group_key: str,
    ) -> list[Suggestion]:
        statement = (
            select(Suggestion)
            .where(
                Suggestion.workspace_id == workspace_id,
                Suggestion.merge_group_key == merge_group_key,
            )
            .order_by(Suggestion.created_at, Suggestion.suggestion_id)
        )
        return list(session.scalars(statement))

    def update_suggestion_status(
        self,
        session: Session,
        suggestion_id: uuid.UUID,
        status: str,
    ) -> Suggestion | None:
        suggestion = self.get_suggestion(session, suggestion_id)
        if suggestion is None:
            return None
        suggestion.status = status
        session.flush()
        return suggestion

    def set_current_analysis(
        self,
        session: Session,
        suggestion_id: uuid.UUID,
        analysis_id: uuid.UUID | None,
    ) -> Suggestion | None:
        suggestion = self.get_suggestion(session, suggestion_id)
        if suggestion is None:
            return None
        suggestion.current_analysis_id = analysis_id
        session.flush()
        return suggestion

    def create_source(
        self,
        session: Session,
        *,
        suggestion_id: uuid.UUID,
        workspace_id: uuid.UUID,
        party_id: uuid.UUID,
        review_input_id: uuid.UUID,
        excerpt: str,
        content_hash: str,
        localized_claim: str,
        stance: str | None,
        span_refs: dict[str, Any] | list[Any],
        status: str,
    ) -> SuggestionSource:
        source = SuggestionSource(
            suggestion_id=suggestion_id,
            workspace_id=workspace_id,
            party_id=party_id,
            review_input_id=review_input_id,
            excerpt=excerpt,
            content_hash=content_hash,
            localized_claim=localized_claim,
            stance=stance,
            span_refs=span_refs,
            status=status,
        )
        return add_and_flush(session, source)

    def get_source(
        self, session: Session, source_id: uuid.UUID
    ) -> SuggestionSource | None:
        return get_by_pk(session, SuggestionSource, source_id)

    def get_source_for_workspace(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        source_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> SuggestionSource | None:
        statement = select(SuggestionSource).where(
            SuggestionSource.source_id == source_id,
            SuggestionSource.workspace_id == workspace_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return session.scalars(statement).one_or_none()

    def get_source_settings_override(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        source_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        source = self.get_source_for_workspace(session, workspace_id, source_id)
        if source is None or source.expression_settings_override is None:
            return None
        return dict(source.expression_settings_override)

    def set_source_settings_override(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        source_id: uuid.UUID,
        settings: dict[str, Any],
    ) -> SuggestionSource | None:
        source = self.get_source_for_workspace(session, workspace_id, source_id)
        if source is None:
            return None
        source.expression_settings_override = settings
        session.flush()
        return source

    def clear_source_settings_override(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        source_id: uuid.UUID,
    ) -> SuggestionSource | None:
        source = self.get_source_for_workspace(session, workspace_id, source_id)
        if source is None:
            return None
        source.expression_settings_override = None
        session.flush()
        return source

    def find_source(
        self,
        session: Session,
        suggestion_id: uuid.UUID,
        party_id: uuid.UUID,
        content_hash: str,
    ) -> SuggestionSource | None:
        statement = select(SuggestionSource).where(
            SuggestionSource.suggestion_id == suggestion_id,
            SuggestionSource.party_id == party_id,
            SuggestionSource.content_hash == content_hash,
        )
        return session.scalars(statement).one_or_none()

    def list_sources(
        self,
        session: Session,
        suggestion_id: uuid.UUID,
        *,
        status: str | None = None,
    ) -> list[SuggestionSource]:
        statement = select(SuggestionSource).where(
            SuggestionSource.suggestion_id == suggestion_id
        )
        if status is not None:
            statement = statement.where(SuggestionSource.status == status)
        statement = statement.order_by(
            SuggestionSource.created_at, SuggestionSource.source_id
        )
        return list(session.scalars(statement))

    def update_source_status(
        self,
        session: Session,
        source_id: uuid.UUID,
        status: str,
    ) -> SuggestionSource | None:
        source = self.get_source(session, source_id)
        if source is None:
            return None
        source.status = status
        session.flush()
        return source

    def delete_by_workspace(self, session: Session, workspace_id: uuid.UUID) -> int:
        result = session.execute(
            delete(Suggestion).where(Suggestion.workspace_id == workspace_id)
        )
        return result.rowcount or 0
