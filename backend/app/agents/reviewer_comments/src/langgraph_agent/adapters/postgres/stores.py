"""Postgres 默认 Store 实现（对齐 A4 ports，Session 不外露）。

开发期共用 backend 同一 PostgreSQL / migrations；本模块不维护 Alembic。
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from langgraph_agent.adapters.postgres.base import session_scope
from langgraph_agent.adapters.postgres.db import create_session_factory
from langgraph_agent.adapters.postgres.repositories import (
    AnalysisRepository,
    AuditRepository,
    GraphRunRepository,
    ManuscriptRepository,
    PaperCardRepository,
    ReplyRepository,
    ReviewRepository,
    SuggestionRepository,
    WorkspaceRepository,
    get_effective_response_settings,
)
from langgraph_agent.ports.types import (
    AnalysisContext,
    AnalysisSnapshotRecord,
    ApprovedSourceReplyView,
    GraphRunRecord,
    ManuscriptVersionRecord,
    ModificationFactRecord,
    PaperBaseline,
    PaperCardRecord as PaperCardDict,
    PersistTaskInitResult,
    ReplyContext,
    ReplyDraftRecord,
    ResultRef,
    ReviewInputRecord,
    ReviewPartyRecord,
    SaveAnalysisResult,
    SaveReplyDraftResult,
    SaveReviewDecisionResult,
    SourceReplyRecord,
    SuggestionBundle,
    SuggestionRecord,
    SuggestionSourceRecord,
    WorkspaceRecord,
)

# 合并组内文本高度相似时，保留原始顺序的第一条非空（对齐 graphs/persist.py）。
_CANONICAL_SIMILARITY = 0.85
_ACTIVE_REPLY_RUN_STATUSES = frozenset({"PENDING", "RUNNING", "WAITING_USER"})


def _result_ref(type_: str, id_: UUID | str) -> ResultRef:
    return {"type": type_, "id": str(id_)}


def _workspace_record(entity: Any) -> WorkspaceRecord:
    return {
        "workspace_id": entity.workspace_id,
        "user_id": entity.user_id,
        "title": entity.title,
        "mode": entity.mode,
        "status": entity.status,
        "global_settings": dict(entity.global_settings or {}),
        "schema_version": int(entity.schema_version),
    }


def _party_record(entity: Any) -> ReviewPartyRecord:
    return {
        "party_id": entity.party_id,
        "workspace_id": entity.workspace_id,
        "role": entity.role,
        "display_name": entity.display_name,
        "raw_label": entity.raw_label,
    }


def _suggestion_record(entity: Any) -> SuggestionRecord:
    return {
        "suggestion_id": entity.suggestion_id,
        "workspace_id": entity.workspace_id,
        "canonical_text": entity.canonical_text,
        "status": entity.status,
        "merge_group_key": entity.merge_group_key,
        "conflict_group_key": entity.conflict_group_key,
        "priority": entity.priority,
        "category_ids": list(entity.category_ids or []),
        "input_version": entity.input_version,
        "current_analysis_id": entity.current_analysis_id,
    }


def _source_record(entity: Any) -> SuggestionSourceRecord:
    return {
        "source_id": entity.source_id,
        "suggestion_id": entity.suggestion_id,
        "workspace_id": entity.workspace_id,
        "party_id": entity.party_id,
        "review_input_id": entity.review_input_id,
        "excerpt": entity.excerpt,
        "content_hash": entity.content_hash,
        "localized_claim": entity.localized_claim,
        "stance": entity.stance,
        "span_refs": entity.span_refs if entity.span_refs is not None else {},
        "status": entity.status,
        "expression_settings_override": (
            dict(entity.expression_settings_override)
            if entity.expression_settings_override is not None
            else None
        ),
    }


def _snapshot_record(entity: Any) -> AnalysisSnapshotRecord:
    return {
        "analysis_id": entity.analysis_id,
        "suggestion_id": entity.suggestion_id,
        "workspace_id": entity.workspace_id,
        "run_id": entity.run_id,
        "input_version": entity.input_version,
        "categories": entity.categories,
        "evidence_items": list(entity.evidence_items or []),
        "coverage": entity.coverage,
        "priority": entity.priority,
        "recommended_actions": entity.recommended_actions,
        "confidence": entity.confidence,
        "status": entity.status,
        "confirmed_at": entity.confirmed_at,
        "confirmed_by": entity.confirmed_by,
    }


def _fact_record(entity: Any) -> ModificationFactRecord:
    return {
        "fact_id": entity.fact_id,
        "suggestion_id": entity.suggestion_id,
        "workspace_id": entity.workspace_id,
        "action_type": entity.action_type,
        "paper_change_summary": entity.paper_change_summary,
        "response_fact_summary": entity.response_fact_summary,
        "constraints": entity.constraints,
        "status": entity.status,
        "input_version": entity.input_version,
        "confirmed_at": entity.confirmed_at,
        "confirmed_by": entity.confirmed_by,
    }


def _reply_record(entity: Any) -> SourceReplyRecord:
    return {
        "reply_id": entity.reply_id,
        "source_id": entity.source_id,
        "suggestion_id": entity.suggestion_id,
        "workspace_id": entity.workspace_id,
        "strategy": dict(entity.strategy or {}),
        "expression_settings": dict(entity.expression_settings or {}),
        "response_facts": entity.response_facts,
        "status": entity.status,
        "current_draft_id": entity.current_draft_id,
        "input_version": entity.input_version,
    }


def _draft_record(entity: Any) -> ReplyDraftRecord:
    return {
        "draft_id": entity.draft_id,
        "reply_id": entity.reply_id,
        "version_no": int(entity.version_no),
        "content": entity.content,
        "language": entity.language,
        "consistency_report": entity.consistency_report,
        "status": entity.status,
        "run_id": entity.run_id,
        "approved_at": entity.approved_at,
        "approved_by": entity.approved_by,
    }


def _manuscript_record(entity: Any) -> ManuscriptVersionRecord:
    return {
        "manuscript_version_id": entity.manuscript_version_id,
        "workspace_id": entity.workspace_id,
        "version_no": int(entity.version_no),
        "source_type": entity.source_type,
        "storage_uri": entity.storage_uri,
        "content_hash": entity.content_hash,
        "parse_status": entity.parse_status,
        "structure_summary": dict(entity.structure_summary or {}),
        "is_baseline": bool(entity.is_baseline),
    }


def _paper_card_record(entity: Any) -> PaperCardDict:
    return {
        "paper_card_id": entity.paper_card_id,
        "workspace_id": entity.workspace_id,
        "manuscript_version_id": entity.manuscript_version_id,
        "card_type": entity.card_type,
        "content": entity.content,
        "source_sections": list(entity.source_sections or []),
        "source_quote": entity.source_quote,
        "confidence": float(entity.confidence),
        "confirmation_status": entity.confirmation_status,
    }


def _graph_run_record(entity: Any) -> GraphRunRecord:
    return {
        "run_id": entity.run_id,
        "workspace_id": entity.workspace_id,
        "graph_name": entity.graph_name,
        "thread_id": entity.thread_id,
        "target_type": entity.target_type,
        "target_id": entity.target_id,
        "input_version": entity.input_version,
        "status": entity.status,
        "attempt": int(entity.attempt),
        "error_code": entity.error_code,
        "error_message": entity.error_message,
        "result_refs": entity.result_refs,
        "parent_run_id": entity.parent_run_id,
        "started_at": entity.started_at,
        "finished_at": entity.finished_at,
    }


def choose_canonical_text(texts: list[str]) -> str:
    """合并组 canonical_text：优先更长更完整；高度相似则取第一条非空。"""
    cleaned = [str(text).strip() for text in texts if str(text or "").strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    longest = max(cleaned, key=len)
    highly_similar = all(
        SequenceMatcher(None, item.casefold(), longest.casefold()).ratio()
        >= _CANONICAL_SIMILARITY
        for item in cleaned
    )
    if highly_similar:
        return cleaned[0]
    return longest


def _group_confirmed_suggestions(
    suggestions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for suggestion in suggestions:
        merge_key = suggestion.get("merge_group_key")
        group_key = str(merge_key or suggestion["proposal_id"])
        grouped[group_key].append(suggestion)

    persistable: list[dict[str, Any]] = []
    for group in grouped.values():
        first = group[0]
        sources = [source for item in group for source in item.get("sources", [])]
        persistable.append(
            {
                "canonical_text": choose_canonical_text(
                    [str(item.get("canonical_text") or "") for item in group]
                ),
                "merge_group_key": first.get("merge_group_key"),
                "conflict_group_key": next(
                    (
                        item.get("conflict_group_key")
                        for item in group
                        if item.get("conflict_group_key")
                    ),
                    first.get("conflict_group_key"),
                ),
                "sources": sources,
            }
        )
    return persistable


def _find_existing_suggestion(
    existing: list[Any],
    *,
    input_version: str,
    proposal: dict[str, Any],
):
    for suggestion in existing:
        if (
            suggestion.input_version == input_version
            and suggestion.canonical_text == proposal["canonical_text"]
            and suggestion.merge_group_key == proposal.get("merge_group_key")
            and suggestion.conflict_group_key == proposal.get("conflict_group_key")
        ):
            return suggestion
    return None


def _stale_consistency_report(previous: object) -> dict[str, Any]:
    prev = previous if isinstance(previous, dict) else {}
    return {
        "is_consistent": True,
        "passed": True,
        "issues": [],
        "items": [],
        "cross_source_conflicts": [],
        "reminders": ["用户手改后尚未重新做一致性检查；保存前的旧检查结果已失效"],
        "summary": "用户手改后尚未重新检查，旧问题已清空，避免误导。",
        "stale": True,
        "stale_reason": "USER_EDIT_OR_REOPEN",
        "previous_is_consistent": prev.get("is_consistent", prev.get("passed")),
    }


def _empty_paper_baseline() -> PaperBaseline:
    return {
        "has_baseline": False,
        "manuscript_version_id": None,
        "abstract": "",
        "sections": [],
        "cards": [],
    }


def _load_paper_baseline(
    session: Session,
    *,
    workspace_id: UUID,
    manuscript_version_id: UUID | None,
    manuscript_repo: ManuscriptRepository,
    card_repo: PaperCardRepository,
) -> PaperBaseline:
    if manuscript_version_id is None:
        return _empty_paper_baseline()
    manuscript = manuscript_repo.get_by_id(session, manuscript_version_id)
    if manuscript is None:
        raise ValueError("分析绑定的论文版本不存在")
    if manuscript.workspace_id != workspace_id:
        raise ValueError("分析绑定的论文版本不属于当前 Workspace")
    if manuscript.parse_status != "SUCCEEDED":
        raise ValueError("分析绑定的论文版本尚未解析成功")
    summary = manuscript.structure_summary or {}
    if not isinstance(summary, dict):
        summary = {}
    sections = summary.get("sections") or []
    if not isinstance(sections, list):
        sections = []
    abstract = str(summary.get("abstract") or "")
    records = card_repo.list_confirmed(
        session, workspace_id, manuscript.manuscript_version_id
    )
    cards = [
        {
            "card_type": record.card_type,
            "content": record.content,
            "source_sections": list(record.source_sections or []),
            "source_quote": record.source_quote,
            "confidence": record.confidence,
            "confirmation_status": record.confirmation_status,
        }
        for record in records
    ]
    return {
        "has_baseline": True,
        "manuscript_version_id": str(manuscript.manuscript_version_id),
        "abstract": abstract,
        "sections": [
            {
                "original_heading": str(item.get("original_heading") or ""),
                "normalized_type": str(item.get("normalized_type") or ""),
                "pages": list(item.get("pages") or []) if isinstance(item, dict) else [],
                "confidence": item.get("confidence") if isinstance(item, dict) else None,
            }
            for item in sections
            if isinstance(item, dict)
        ],
        "cards": cards,
    }


def _supersede_active_reply_run(
    session: Session,
    *,
    workspace_id: UUID,
    source_id: UUID,
    actor_user_id: str,
    primary_source_id: UUID,
    graph_run_repo: GraphRunRepository,
    audit_repo: AuditRepository,
) -> str | None:
    active = graph_run_repo.get_active_source_reply_run(session, workspace_id, source_id)
    if active is None or active.status not in _ACTIVE_REPLY_RUN_STATUSES:
        return None
    graph_run_repo.update_status(
        session,
        active.run_id,
        status="SUPERSEDED",
        error_code="SUPERSEDED_BY_SIBLING_APPROVE",
        error_message="同建议其他来源已批准，本条回复图任务已被覆盖同步，无需再单独确认。",
        finished_at=datetime.now(timezone.utc),
    )
    audit_repo.create(
        session,
        workspace_id=workspace_id,
        target_type="SOURCE",
        target_id=source_id,
        action="SUPERSEDE",
        payload={
            "via": "propagate_from_sibling",
            "primary_source_id": str(primary_source_id),
            "superseded_run_id": str(active.run_id),
            "previous_status": active.status,
        },
        actor_user_id=actor_user_id,
    )
    return str(active.run_id)


def _propagate_approved_reply_to_siblings(
    session: Session,
    *,
    workspace_id: UUID,
    primary_source_id: UUID,
    primary_reply: Any,
    approved_draft: Any,
    actor_user_id: str,
) -> list[dict[str, str]]:
    """对齐 backend graphs/reply_sync.propagate_approved_reply_to_siblings。"""
    suggestion_id = primary_reply.suggestion_id
    suggestion_repo = SuggestionRepository()
    reply_repo = ReplyRepository()
    audit_repo = AuditRepository()
    graph_run_repo = GraphRunRepository()
    sibling_sources = [
        source
        for source in suggestion_repo.list_sources(session, suggestion_id)
        if source.source_id != primary_source_id and source.workspace_id == workspace_id
    ]
    if not sibling_sources:
        return []

    approved_at = approved_draft.approved_at or datetime.now(timezone.utc)
    synced: list[dict[str, str]] = []
    for sibling in sibling_sources:
        superseded_run_id = _supersede_active_reply_run(
            session,
            workspace_id=workspace_id,
            source_id=sibling.source_id,
            actor_user_id=actor_user_id,
            primary_source_id=primary_source_id,
            graph_run_repo=graph_run_repo,
            audit_repo=audit_repo,
        )
        sibling_reply = reply_repo.get_by_source_id(session, sibling.source_id)
        if sibling_reply is None:
            sibling_reply = reply_repo.create_reply(
                session,
                source_id=sibling.source_id,
                suggestion_id=suggestion_id,
                workspace_id=workspace_id,
                strategy=dict(primary_reply.strategy or {}),
                expression_settings=dict(primary_reply.expression_settings or {}),
                response_facts=(
                    primary_reply.response_facts
                    if isinstance(primary_reply.response_facts, (dict, list))
                    else {}
                ),
                status="APPROVED",
                current_draft_id=None,
                input_version=str(primary_reply.input_version),
            )
        else:
            sibling_reply.strategy = dict(primary_reply.strategy or {})
            sibling_reply.expression_settings = dict(
                primary_reply.expression_settings or {}
            )
            sibling_reply.response_facts = (
                primary_reply.response_facts
                if isinstance(primary_reply.response_facts, (dict, list))
                else {}
            )
            sibling_reply.input_version = str(primary_reply.input_version)
            reply_repo.update_reply_status(session, sibling_reply.reply_id, "APPROVED")

        drafts = reply_repo.list_drafts(session, sibling_reply.reply_id)
        current = next(
            (
                item
                for item in drafts
                if item.draft_id == sibling_reply.current_draft_id
            ),
            None,
        )
        if (
            current is not None
            and current.status == "APPROVED"
            and current.content == approved_draft.content
        ):
            item = {
                "source_id": str(sibling.source_id),
                "reply_id": str(sibling_reply.reply_id),
                "draft_id": str(current.draft_id),
                "mode": "already_matched",
            }
            if superseded_run_id:
                item["superseded_run_id"] = superseded_run_id
            synced.append(item)
            continue

        next_version = max((item.version_no for item in drafts), default=0) + 1
        report = approved_draft.consistency_report
        if isinstance(report, dict):
            report = {
                **report,
                "propagated_from_source_id": str(primary_source_id),
                "propagated_from_draft_id": str(approved_draft.draft_id),
            }
        else:
            report = {
                "is_consistent": True,
                "passed": True,
                "issues": [],
                "items": [],
                "cross_source_conflicts": [],
                "reminders": ["由同建议其他来源批准结果同步"],
                "propagated_from_source_id": str(primary_source_id),
                "propagated_from_draft_id": str(approved_draft.draft_id),
            }
        created = reply_repo.create_draft(
            session,
            reply_id=sibling_reply.reply_id,
            version_no=next_version,
            content=approved_draft.content,
            language=approved_draft.language,
            consistency_report=report,
            status="APPROVED",
            run_id=None,
            approved_at=approved_at,
            approved_by=actor_user_id,
        )
        reply_repo.set_current_draft(session, sibling_reply.reply_id, created.draft_id)
        reply_repo.update_reply_status(session, sibling_reply.reply_id, "APPROVED")
        audit_repo.create(
            session,
            workspace_id=workspace_id,
            target_type="DRAFT",
            target_id=created.draft_id,
            action="CONFIRM",
            payload={
                "via": "propagate_from_sibling",
                "primary_source_id": str(primary_source_id),
                "primary_draft_id": str(approved_draft.draft_id),
                "source_id": str(sibling.source_id),
                "version_no": next_version,
                "superseded_run_id": superseded_run_id,
            },
            actor_user_id=actor_user_id,
        )
        item = {
            "source_id": str(sibling.source_id),
            "reply_id": str(sibling_reply.reply_id),
            "draft_id": str(created.draft_id),
            "mode": "copied",
        }
        if superseded_run_id:
            item["superseded_run_id"] = superseded_run_id
        synced.append(item)
    return synced


class _SessionStoreBase:
    """持有 session_factory 的 Store 基类。"""

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = (
            session_factory if session_factory is not None else create_session_factory()
        )


class PostgresWorkspaceStore(_SessionStoreBase):
    """WorkspaceStore 的 Postgres 实现。"""

    def get_workspace(self, workspace_id: UUID) -> WorkspaceRecord | None:
        repo = WorkspaceRepository()
        with session_scope(self._session_factory) as session:
            entity = repo.get_by_id(session, workspace_id)
            if entity is None:
                return None
            return _workspace_record(entity)

    def list_current_review_inputs(self, workspace_id: UUID) -> list[ReviewInputRecord]:
        review_repo = ReviewRepository()
        with session_scope(self._session_factory) as session:
            parties = {
                party.party_id: party
                for party in review_repo.list_parties(session, workspace_id)
            }
            current_inputs = review_repo.list_inputs(
                session, workspace_id, current_only=True
            )
            result: list[ReviewInputRecord] = []
            for review_input in current_inputs:
                party = parties.get(review_input.party_id)
                if party is None:
                    raise ValueError(
                        f"ReviewInput 缺少 ReviewParty：{review_input.review_input_id}"
                    )
                result.append(
                    {
                        "review_input_id": review_input.review_input_id,
                        "workspace_id": review_input.workspace_id,
                        "party_id": review_input.party_id,
                        "version_no": int(review_input.version_no),
                        "raw_text": review_input.raw_text,
                        "storage_uri": review_input.storage_uri,
                        "content_hash": review_input.content_hash,
                        "language": review_input.language,
                        "is_current": bool(review_input.is_current),
                        "role": party.role,
                        "display_name": party.display_name,
                        "raw_label": party.raw_label,
                    }
                )
            return result

    def list_parties(self, workspace_id: UUID) -> list[ReviewPartyRecord]:
        review_repo = ReviewRepository()
        with session_scope(self._session_factory) as session:
            return [
                _party_record(party)
                for party in review_repo.list_parties(session, workspace_id)
            ]

    def persist_task_init_result(
        self,
        *,
        workspace_id: UUID,
        input_version: str,
        confirmed_suggestions: list[dict[str, Any]],
    ) -> PersistTaskInitResult:
        if not isinstance(confirmed_suggestions, list):
            raise ValueError("缺少已确认、可落库的建议提案")
        suggestion_repo = SuggestionRepository()
        workspace_repo = WorkspaceRepository()
        result_refs: list[ResultRef] = []
        with session_scope(self._session_factory) as session:
            workspace = workspace_repo.update_status(session, workspace_id, "ACTIVE")
            if workspace is None:
                raise ValueError(f"Workspace 不存在：{workspace_id}")
            existing = suggestion_repo.list_suggestions(session, workspace_id)
            for proposal in _group_confirmed_suggestions(confirmed_suggestions):
                suggestion = _find_existing_suggestion(
                    existing,
                    input_version=input_version,
                    proposal=proposal,
                )
                if suggestion is None:
                    suggestion = suggestion_repo.create_suggestion(
                        session,
                        workspace_id=workspace_id,
                        canonical_text=proposal["canonical_text"],
                        status="PENDING",
                        merge_group_key=proposal.get("merge_group_key"),
                        conflict_group_key=proposal.get("conflict_group_key"),
                        priority=None,
                        category_ids=[],
                        input_version=input_version,
                        current_analysis_id=None,
                    )
                    existing.append(suggestion)
                result_refs.append(
                    _result_ref("suggestion", suggestion.suggestion_id)
                )
                for source in proposal["sources"]:
                    party_id = UUID(str(source["party_id"]))
                    content_hash = hashlib.sha256(
                        source["excerpt"].encode("utf-8")
                    ).hexdigest()
                    persisted_source = suggestion_repo.find_source(
                        session,
                        suggestion.suggestion_id,
                        party_id,
                        content_hash,
                    )
                    if persisted_source is None:
                        persisted_source = suggestion_repo.create_source(
                            session,
                            suggestion_id=suggestion.suggestion_id,
                            workspace_id=workspace_id,
                            party_id=party_id,
                            review_input_id=UUID(str(source["review_input_id"])),
                            excerpt=source["excerpt"],
                            content_hash=content_hash,
                            localized_claim=source["localized_claim"],
                            stance=source.get("stance"),
                            span_refs=source.get("span_refs", {}),
                            status="ACTIVE",
                        )
                    result_refs.append(
                        _result_ref("suggestion_source", persisted_source.source_id)
                    )
            return {
                "result_refs": result_refs,
                "workspace_status": workspace.status,
            }


class PostgresSuggestionStore(_SessionStoreBase):
    """SuggestionStore 的 Postgres 实现。"""

    def load_suggestion_bundle(
        self,
        suggestion_id: UUID,
        *,
        workspace_id: UUID | None = None,
        source_status: str | None = "ACTIVE",
    ) -> SuggestionBundle:
        repo = SuggestionRepository()
        with session_scope(self._session_factory) as session:
            suggestion = repo.get_suggestion(session, suggestion_id)
            if suggestion is None:
                raise ValueError("Suggestion 不存在")
            if workspace_id is not None and suggestion.workspace_id != workspace_id:
                raise ValueError("Suggestion 不属于指定 Workspace")
            sources = repo.list_sources(
                session, suggestion_id, status=source_status
            )
            if source_status == "ACTIVE" and not sources:
                raise ValueError("Suggestion 没有 ACTIVE 来源")
            return {
                "suggestion": _suggestion_record(suggestion),
                "sources": [_source_record(item) for item in sources],
            }


class PostgresAnalysisStore(_SessionStoreBase):
    """AnalysisStore 的 Postgres 实现。"""

    def load_analysis_context(
        self,
        *,
        workspace_id: UUID,
        suggestion_id: UUID,
        input_version: str,
        manuscript_version_id: UUID | None = None,
    ) -> AnalysisContext:
        suggestion_repo = SuggestionRepository()
        analysis_repo = AnalysisRepository()
        manuscript_repo = ManuscriptRepository()
        card_repo = PaperCardRepository()
        with session_scope(self._session_factory) as session:
            suggestion = suggestion_repo.get_suggestion(session, suggestion_id)
            if suggestion is None or suggestion.workspace_id != workspace_id:
                raise ValueError("Suggestion 不存在或不属于当前 Workspace")
            sources = suggestion_repo.list_sources(
                session, suggestion_id, status="ACTIVE"
            )
            snapshot = analysis_repo.get_current_snapshot(session, suggestion_id)
            confirmed_facts = [
                _fact_record(fact)
                for fact in analysis_repo.list_facts(
                    session, suggestion_id, status="CONFIRMED"
                )
                if fact.input_version == input_version
            ]
            reusable = bool(
                snapshot is not None
                and snapshot.status == "CONFIRMED"
                and snapshot.input_version == input_version
            )
            paper_baseline = _load_paper_baseline(
                session,
                workspace_id=workspace_id,
                manuscript_version_id=manuscript_version_id,
                manuscript_repo=manuscript_repo,
                card_repo=card_repo,
            )
            return {
                "suggestion": _suggestion_record(suggestion),
                "sources": [_source_record(item) for item in sources],
                "current_snapshot": (
                    _snapshot_record(snapshot) if snapshot is not None else None
                ),
                "confirmed_facts": confirmed_facts,
                "paper_baseline": paper_baseline,
                "reusable": reusable,
            }

    def save_analysis_snapshot(
        self,
        *,
        suggestion_id: UUID,
        workspace_id: UUID,
        run_id: UUID,
        input_version: str,
        user_id: str,
        classification: dict[str, Any],
        evidence: dict[str, Any],
        priority: dict[str, Any],
        recommended_actions: dict[str, Any] | list[dict[str, Any]],
        classification_confirmed_by_user: bool = False,
    ) -> AnalysisSnapshotRecord:
        analysis_repo = AnalysisRepository()
        suggestion_repo = SuggestionRepository()
        audit_repo = AuditRepository()
        with session_scope(self._session_factory) as session:
            existing = analysis_repo.get_by_run_id(session, run_id)
            if existing is not None:
                if existing.status != "CONFIRMED":
                    raise RuntimeError("同一 run_id 已存在非 CONFIRMED AnalysisSnapshot")
                return _snapshot_record(existing)

            suggestion = suggestion_repo.get_suggestion(session, suggestion_id)
            if suggestion is None or suggestion.workspace_id != workspace_id:
                raise ValueError("Suggestion 不存在或不属于当前 Workspace")
            if suggestion.input_version != input_version:
                raise ValueError("Suggestion 输入版本与分析运行不匹配")

            actions_payload: list[dict[str, Any]] | dict[str, Any]
            if isinstance(recommended_actions, dict):
                actions_payload = list(recommended_actions.get("recommendations", []))
            else:
                actions_payload = list(recommended_actions)

            now = datetime.now(timezone.utc)
            snapshot = analysis_repo.create_snapshot(
                session,
                suggestion_id=suggestion_id,
                workspace_id=workspace_id,
                run_id=run_id,
                input_version=input_version,
                categories=classification,
                evidence_items=list(evidence.get("evidence_items", [])),
                coverage=str(evidence.get("coverage", "UNKNOWN")),
                priority=str(priority["work_priority"]),
                recommended_actions=actions_payload,
                confidence=float(classification["classification_confidence"]),
                status="CONFIRMED",
                confirmed_at=now,
                confirmed_by=user_id,
            )
            if classification_confirmed_by_user:
                audit_repo.create(
                    session,
                    workspace_id=workspace_id,
                    target_type="ANALYSIS",
                    target_id=snapshot.analysis_id,
                    action="CONFIRM",
                    payload={"classification": classification},
                    actor_user_id=user_id,
                )
            suggestion_repo.set_current_analysis(
                session, suggestion_id, snapshot.analysis_id
            )
            suggestion_repo.update_suggestion_status(
                session, suggestion_id, "SUCCEEDED"
            )
            return _snapshot_record(snapshot)

    def save_modification_facts(
        self,
        *,
        suggestion_id: UUID,
        workspace_id: UUID,
        input_version: str,
        user_id: str,
        fact_proposals: list[dict[str, Any]],
    ) -> list[ModificationFactRecord]:
        if not isinstance(fact_proposals, list) or not fact_proposals:
            raise ValueError("缺少已确认的 ModificationFact 提案")
        analysis_repo = AnalysisRepository()
        audit_repo = AuditRepository()
        now = datetime.now(timezone.utc)
        with session_scope(self._session_factory) as session:
            created: list[ModificationFactRecord] = []
            for proposal in fact_proposals:
                fact = analysis_repo.create_fact(
                    session,
                    suggestion_id=suggestion_id,
                    workspace_id=workspace_id,
                    action_type=str(proposal["action_type"]),
                    paper_change_summary=str(proposal["paper_change_summary"]),
                    response_fact_summary=str(proposal["response_fact_summary"]),
                    constraints=dict(proposal["constraints"]),
                    status="CONFIRMED",
                    input_version=input_version,
                    confirmed_at=now,
                    confirmed_by=user_id,
                )
                audit_repo.create(
                    session,
                    workspace_id=workspace_id,
                    target_type="FACT",
                    target_id=fact.fact_id,
                    action="CONFIRM",
                    payload={
                        "action_type": fact.action_type,
                        "decision_status": proposal.get("decision_status"),
                        "execution_status": proposal.get("execution_status"),
                    },
                    actor_user_id=user_id,
                )
                created.append(_fact_record(fact))
            return created

    def save_analysis_result(
        self,
        *,
        suggestion_id: UUID,
        workspace_id: UUID,
        run_id: UUID,
        input_version: str,
        user_id: str,
        classification: dict[str, Any],
        evidence: dict[str, Any],
        priority: dict[str, Any],
        recommended_actions: dict[str, Any] | list[dict[str, Any]],
        fact_proposals: list[dict[str, Any]],
        classification_confirmed_by_user: bool = False,
    ) -> SaveAnalysisResult:
        """单事务写入快照 + 修改事实（对齐 analysis_persist.persist_analysis）。"""
        if not isinstance(fact_proposals, list) or not fact_proposals:
            raise ValueError("缺少已确认的 ModificationFact 提案")
        analysis_repo = AnalysisRepository()
        suggestion_repo = SuggestionRepository()
        audit_repo = AuditRepository()
        with session_scope(self._session_factory) as session:
            existing = analysis_repo.get_by_run_id(session, run_id)
            if existing is not None:
                if existing.status != "CONFIRMED":
                    raise RuntimeError("同一 run_id 已存在非 CONFIRMED AnalysisSnapshot")
                facts = [
                    _fact_record(fact)
                    for fact in analysis_repo.list_facts(
                        session, suggestion_id, status="CONFIRMED"
                    )
                    if fact.input_version == input_version
                ]
                refs = [_result_ref("analysis", existing.analysis_id)]
                refs.extend(
                    _result_ref("modification_fact", fact["fact_id"]) for fact in facts
                )
                return {
                    "snapshot": _snapshot_record(existing),
                    "facts": facts,
                    "result_refs": refs,
                    "reused": True,
                }

            suggestion = suggestion_repo.get_suggestion(session, suggestion_id)
            if suggestion is None or suggestion.workspace_id != workspace_id:
                raise ValueError("Suggestion 不存在或不属于当前 Workspace")
            if suggestion.input_version != input_version:
                raise ValueError("Suggestion 输入版本与分析运行不匹配")

            actions_payload: list[dict[str, Any]] | dict[str, Any]
            if isinstance(recommended_actions, dict):
                actions_payload = list(recommended_actions.get("recommendations", []))
            else:
                actions_payload = list(recommended_actions)

            now = datetime.now(timezone.utc)
            snapshot = analysis_repo.create_snapshot(
                session,
                suggestion_id=suggestion_id,
                workspace_id=workspace_id,
                run_id=run_id,
                input_version=input_version,
                categories=classification,
                evidence_items=list(evidence.get("evidence_items", [])),
                coverage=str(evidence.get("coverage", "UNKNOWN")),
                priority=str(priority["work_priority"]),
                recommended_actions=actions_payload,
                confidence=float(classification["classification_confidence"]),
                status="CONFIRMED",
                confirmed_at=now,
                confirmed_by=user_id,
            )
            result_refs: list[ResultRef] = [
                _result_ref("analysis", snapshot.analysis_id)
            ]
            facts: list[ModificationFactRecord] = []
            for proposal in fact_proposals:
                fact = analysis_repo.create_fact(
                    session,
                    suggestion_id=suggestion_id,
                    workspace_id=workspace_id,
                    action_type=str(proposal["action_type"]),
                    paper_change_summary=str(proposal["paper_change_summary"]),
                    response_fact_summary=str(proposal["response_fact_summary"]),
                    constraints=dict(proposal["constraints"]),
                    status="CONFIRMED",
                    input_version=input_version,
                    confirmed_at=now,
                    confirmed_by=user_id,
                )
                result_refs.append(_result_ref("modification_fact", fact.fact_id))
                audit_repo.create(
                    session,
                    workspace_id=workspace_id,
                    target_type="FACT",
                    target_id=fact.fact_id,
                    action="CONFIRM",
                    payload={
                        "action_type": fact.action_type,
                        "decision_status": proposal.get("decision_status"),
                        "execution_status": proposal.get("execution_status"),
                    },
                    actor_user_id=user_id,
                )
                facts.append(_fact_record(fact))

            if classification_confirmed_by_user:
                audit_repo.create(
                    session,
                    workspace_id=workspace_id,
                    target_type="ANALYSIS",
                    target_id=snapshot.analysis_id,
                    action="CONFIRM",
                    payload={"classification": classification},
                    actor_user_id=user_id,
                )
            suggestion_repo.set_current_analysis(
                session, suggestion_id, snapshot.analysis_id
            )
            suggestion_repo.update_suggestion_status(
                session, suggestion_id, "SUCCEEDED"
            )
            return {
                "snapshot": _snapshot_record(snapshot),
                "facts": facts,
                "result_refs": result_refs,
                "reused": False,
            }


class PostgresReplyStore(_SessionStoreBase):
    """ReplyStore 的 Postgres 实现。"""

    def load_reply_context(
        self,
        *,
        workspace_id: UUID,
        suggestion_id: UUID,
        source_id: UUID,
        expression_settings: dict[str, Any] | None = None,
    ) -> ReplyContext:
        workspace_repo = WorkspaceRepository()
        suggestion_repo = SuggestionRepository()
        analysis_repo = AnalysisRepository()
        reply_repo = ReplyRepository()
        with session_scope(self._session_factory) as session:
            workspace = workspace_repo.get_by_id(session, workspace_id)
            source = suggestion_repo.get_source(session, source_id)
            if workspace is None:
                raise ValueError("Workspace 不存在")
            if (
                source is None
                or source.workspace_id != workspace_id
                or source.suggestion_id != suggestion_id
            ):
                raise ValueError("SuggestionSource 不存在或运行目标不匹配")
            snapshot = analysis_repo.get_current_snapshot(session, suggestion_id)
            facts = analysis_repo.list_facts(
                session, suggestion_id, status="CONFIRMED"
            )
            if snapshot is not None:
                facts = [
                    fact
                    for fact in facts
                    if fact.input_version == snapshot.input_version
                ]
            analysis_ready = bool(
                snapshot is not None and snapshot.status == "CONFIRMED" and facts
            )
            effective = get_effective_response_settings(workspace, source)
            if expression_settings is not None and expression_settings != effective:
                raise ValueError("回复表达设置已变化，请重新运行")

            confirmed_analysis = None
            if snapshot is not None and snapshot.status == "CONFIRMED":
                confirmed_analysis = _snapshot_record(snapshot)

            approved: list[ApprovedSourceReplyView] = []
            for other in reply_repo.list_by_suggestion(session, suggestion_id):
                if other.source_id == source_id or other.status != "APPROVED":
                    continue
                current = reply_repo.get_current_draft(session, other.reply_id)
                if current is None or current.status != "APPROVED":
                    continue
                linked_ids = (
                    other.response_facts.get("linked_fact_ids", [])
                    if isinstance(other.response_facts, dict)
                    else []
                )
                approved.append(
                    {
                        "source_id": other.source_id,
                        "generated_content": current.content,
                        "linked_fact_ids": list(linked_ids),
                    }
                )
            return {
                "analysis_ready": analysis_ready,
                "source": _source_record(source),
                "expression_settings": effective,
                "confirmed_analysis": confirmed_analysis,
                "confirmed_modification_facts": [_fact_record(f) for f in facts],
                "other_approved_replies": approved,
            }

    def save_reply_draft(
        self,
        *,
        workspace_id: UUID,
        suggestion_id: UUID,
        source_id: UUID,
        run_id: UUID,
        input_version: str,
        user_id: str,
        strategy: dict[str, Any],
        expression_settings: dict[str, Any],
        response_facts: dict[str, Any] | list[Any],
        generated_draft: dict[str, Any],
        consistency_report: dict[str, Any] | list[Any],
    ) -> SaveReplyDraftResult:
        reply_repo = ReplyRepository()
        audit_repo = AuditRepository()
        with session_scope(self._session_factory) as session:
            reply = reply_repo.get_by_source_id(session, source_id)
            if reply is not None:
                run_drafts = [
                    draft
                    for draft in reply_repo.list_drafts(session, reply.reply_id)
                    if draft.run_id == run_id
                ]
                existing_draft = (
                    max(run_drafts, key=lambda item: item.version_no)
                    if run_drafts
                    else None
                )
                if existing_draft is not None:
                    return {
                        "reply": _reply_record(reply),
                        "draft": _draft_record(existing_draft),
                        "result_refs": [
                            _result_ref("source_reply", reply.reply_id),
                            _result_ref("reply_draft", existing_draft.draft_id),
                        ],
                        "phase": "REVIEW_DRAFT",
                        "reused": True,
                    }
                if reply.input_version != input_version:
                    for old_draft in reply_repo.list_drafts(session, reply.reply_id):
                        if old_draft.status not in {"APPROVED", "STALE"}:
                            reply_repo.update_draft_status(
                                session,
                                old_draft.draft_id,
                                status="STALE",
                                approved_at=None,
                                approved_by=None,
                            )
                reply.strategy = strategy
                reply.expression_settings = expression_settings
                reply.response_facts = response_facts
                reply.status = "REVIEW_WAITING"
                reply.input_version = input_version
                session.flush()
            else:
                reply = reply_repo.create_reply(
                    session,
                    source_id=source_id,
                    suggestion_id=suggestion_id,
                    workspace_id=workspace_id,
                    strategy=strategy,
                    expression_settings=expression_settings,
                    response_facts=response_facts,
                    status="REVIEW_WAITING",
                    current_draft_id=None,
                    input_version=input_version,
                )

            drafts = reply_repo.list_drafts(session, reply.reply_id)
            next_version = max((item.version_no for item in drafts), default=0) + 1
            created = reply_repo.create_draft(
                session,
                reply_id=reply.reply_id,
                version_no=next_version,
                content=str(generated_draft["generated_content"]),
                language=str(generated_draft["language"]),
                consistency_report=consistency_report,
                status="GENERATED",
                run_id=run_id,
                approved_at=None,
                approved_by=None,
            )
            reply_repo.set_current_draft(session, reply.reply_id, created.draft_id)
            audit_repo.create(
                session,
                workspace_id=workspace_id,
                target_type="REPLY",
                target_id=reply.reply_id,
                action="CONFIRM",
                payload={"strategy": strategy},
                actor_user_id=user_id,
            )
            audit_repo.create(
                session,
                workspace_id=workspace_id,
                target_type="REPLY",
                target_id=reply.reply_id,
                action="CONFIRM",
                payload={"response_facts": response_facts},
                actor_user_id=user_id,
            )
            session.refresh(reply)
            return {
                "reply": _reply_record(reply),
                "draft": _draft_record(created),
                "result_refs": [
                    _result_ref("source_reply", reply.reply_id),
                    _result_ref("reply_draft", created.draft_id),
                ],
                "phase": "REVIEW_DRAFT",
                "reused": False,
            }

    def save_review_decision(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        user_id: str,
        reply_id: UUID,
        draft_id: UUID,
        decision: dict[str, Any],
    ) -> SaveReviewDecisionResult:
        reply_repo = ReplyRepository()
        audit_repo = AuditRepository()
        with session_scope(self._session_factory) as session:
            reply = reply_repo.get_reply(session, reply_id)
            current = reply_repo.get_draft(session, draft_id)
            if reply is None or current is None:
                raise ValueError("待审核回复或草稿不存在")
            action = decision.get("action")
            if action == "approve":
                approved_at = datetime.now(timezone.utc)
                reply_repo.update_draft_status(
                    session,
                    current.draft_id,
                    status="APPROVED",
                    approved_at=approved_at,
                    approved_by=user_id,
                )
                reply_repo.update_reply_status(session, reply.reply_id, "APPROVED")
                audit_repo.create(
                    session,
                    workspace_id=workspace_id,
                    target_type="DRAFT",
                    target_id=current.draft_id,
                    action="CONFIRM",
                    payload={"version_no": current.version_no},
                    actor_user_id=user_id,
                )
                session.refresh(reply)
                session.refresh(current)
                synced_sources = _propagate_approved_reply_to_siblings(
                    session,
                    workspace_id=workspace_id,
                    primary_source_id=reply.source_id,
                    primary_reply=reply,
                    approved_draft=current,
                    actor_user_id=user_id,
                )
                return {
                    "reply": _reply_record(reply),
                    "draft": _draft_record(current),
                    "result_refs": [
                        _result_ref("source_reply", reply.reply_id),
                        _result_ref("reply_draft", current.draft_id),
                    ],
                    "phase": "SUCCEEDED",
                    "synced_sources": synced_sources,
                }

            if action != "edit":
                raise ValueError("审核 action 只能是 approve 或 edit")
            edited_content = str(decision.get("content", "")).strip()
            if not edited_content:
                raise ValueError("edit 必须提供非空 content")
            drafts = reply_repo.list_drafts(session, reply.reply_id)
            next_version = max(item.version_no for item in drafts) + 1
            edited = reply_repo.create_draft(
                session,
                reply_id=reply.reply_id,
                version_no=next_version,
                content=edited_content,
                language=current.language,
                consistency_report=_stale_consistency_report(
                    current.consistency_report
                ),
                status="EDITED",
                run_id=run_id,
                approved_at=None,
                approved_by=None,
            )
            reply_repo.set_current_draft(session, reply.reply_id, edited.draft_id)
            reply_repo.update_reply_status(session, reply.reply_id, "REVIEW_WAITING")
            audit_repo.create(
                session,
                workspace_id=workspace_id,
                target_type="DRAFT",
                target_id=edited.draft_id,
                action="EDIT",
                payload={
                    "previous_draft_id": str(current.draft_id),
                    "version_no": next_version,
                },
                actor_user_id=user_id,
            )
            session.refresh(reply)
            return {
                "reply": _reply_record(reply),
                "draft": _draft_record(edited),
                "result_refs": [
                    _result_ref("source_reply", reply.reply_id),
                    _result_ref("reply_draft", edited.draft_id),
                ],
                "phase": "REVIEW_DRAFT",
                "synced_sources": [],
            }


class PostgresManuscriptStore(_SessionStoreBase):
    """ManuscriptStore 的 Postgres 实现。"""

    def get_manuscript_version(
        self, manuscript_version_id: UUID
    ) -> ManuscriptVersionRecord | None:
        repo = ManuscriptRepository()
        with session_scope(self._session_factory) as session:
            entity = repo.get_by_id(session, manuscript_version_id)
            if entity is None:
                return None
            return _manuscript_record(entity)

    def get_paper_cards(
        self,
        workspace_id: UUID,
        manuscript_version_id: UUID,
        *,
        confirmed_only: bool = False,
    ) -> list[PaperCardDict]:
        repo = PaperCardRepository()
        with session_scope(self._session_factory) as session:
            if confirmed_only:
                rows = repo.list_confirmed(
                    session, workspace_id, manuscript_version_id
                )
            else:
                rows = repo.list_by_manuscript(
                    session, workspace_id, manuscript_version_id
                )
            return [_paper_card_record(item) for item in rows]

    def save_baseline_cards(
        self,
        *,
        workspace_id: UUID,
        manuscript_version_id: UUID,
        confirmed_cards: list[dict[str, Any]],
    ) -> list[PaperCardDict]:
        if not isinstance(confirmed_cards, list):
            raise ValueError("缺少已确认的基线卡片")
        paper_card_repo = PaperCardRepository()
        manuscript_repo = ManuscriptRepository()
        with session_scope(self._session_factory) as session:
            manuscript = manuscript_repo.get_by_id(session, manuscript_version_id)
            if manuscript is None:
                raise ValueError("论文版本不存在")
            if manuscript.workspace_id != workspace_id:
                raise ValueError("论文版本不属于当前 Workspace")
            if manuscript.parse_status != "SUCCEEDED":
                raise ValueError("论文解析状态已变化，无法设置基线")
            for item in confirmed_cards:
                action = item.get("action")
                if action == "create":
                    paper_card_repo.create(
                        session,
                        workspace_id=workspace_id,
                        manuscript_version_id=manuscript_version_id,
                        card_type=item["card_type"],
                        content=item["content"],
                        source_sections=item.get("source_sections") or [],
                        source_quote=item.get("source_quote") or "",
                        confidence=float(item.get("confidence", 1.0)),
                        confirmation_status=item["confirmation_status"],
                    )
                elif action == "update":
                    paper_card_id = UUID(str(item["paper_card_id"]))
                    content = item.get("content")
                    content_arg = (
                        content
                        if item.get("confirmation_status") == "EDITED"
                        else None
                    )
                    updated = paper_card_repo.update_confirmation(
                        session,
                        paper_card_id,
                        confirmation_status=item["confirmation_status"],
                        content=content_arg,
                    )
                    if updated is None:
                        raise ValueError(f"基线卡片不存在：{paper_card_id}")
                else:
                    raise ValueError(f"未知基线落库 action：{action}")
            manuscript_repo.set_baseline(
                session, manuscript_version_id, is_baseline=True
            )
            rows = paper_card_repo.list_by_manuscript(
                session, workspace_id, manuscript_version_id
            )
            return [_paper_card_record(item) for item in rows]


class PostgresFinalizeStore(_SessionStoreBase):
    """FinalizeStore 的 Postgres 实现（对齐 backend finalize_graph 读写语义）。"""

    def load_finalize_context(self, workspace_id: UUID) -> dict[str, Any]:
        """只读加载 Workspace、ACTIVE source 与回复当前版本。"""
        workspace_repo = WorkspaceRepository()
        suggestion_repo = SuggestionRepository()
        review_repo = ReviewRepository()
        reply_repo = ReplyRepository()
        analysis_repo = AnalysisRepository()

        with session_scope(self._session_factory) as session:
            workspace = workspace_repo.get_by_id(session, workspace_id)
            if workspace is None:
                raise ValueError(f"Workspace 不存在：{workspace_id}")
            parties = review_repo.list_parties(session, workspace_id)
            party_by_id = {party.party_id: party for party in parties}
            party_order = {
                party.party_id: index for index, party in enumerate(parties)
            }
            replies = reply_repo.list_by_workspace(session, workspace_id)
            reply_by_source = {reply.source_id: reply for reply in replies}

            suggestion_items: list[dict[str, Any]] = []
            source_items: list[dict[str, Any]] = []
            for suggestion in suggestion_repo.list_suggestions(session, workspace_id):
                facts = analysis_repo.list_facts(session, suggestion.suggestion_id)
                fact_items = [
                    {
                        "fact_id": str(fact.fact_id),
                        "action_type": fact.action_type,
                        "paper_change_summary": fact.paper_change_summary,
                        "response_fact_summary": fact.response_fact_summary,
                        "constraints": fact.constraints,
                        "status": fact.status,
                        "input_version": fact.input_version,
                    }
                    for fact in facts
                ]
                sources = suggestion_repo.list_sources(
                    session, suggestion.suggestion_id, status="ACTIVE"
                )
                suggestion_items.append(
                    {
                        "suggestion_id": str(suggestion.suggestion_id),
                        "canonical_text": suggestion.canonical_text,
                        "status": suggestion.status,
                        "priority": suggestion.priority,
                        "category_ids": list(suggestion.category_ids or []),
                        "input_version": suggestion.input_version,
                        "current_analysis_id": (
                            str(suggestion.current_analysis_id)
                            if suggestion.current_analysis_id is not None
                            else None
                        ),
                        "modification_facts": fact_items,
                    }
                )
                for source in sources:
                    party = party_by_id.get(source.party_id)
                    reply = reply_by_source.get(source.source_id)
                    reply_item = None
                    if reply is not None:
                        draft = reply_repo.get_current_draft(session, reply.reply_id)
                        reply_item = {
                            "reply_id": str(reply.reply_id),
                            "source_id": str(reply.source_id),
                            "suggestion_id": str(reply.suggestion_id),
                            "status": reply.status,
                            "strategy": reply.strategy,
                            "expression_settings": reply.expression_settings,
                            "response_facts": reply.response_facts,
                            "input_version": reply.input_version,
                            "current_draft": (
                                {
                                    "draft_id": str(draft.draft_id),
                                    "version_no": draft.version_no,
                                    "content": draft.content,
                                    "language": draft.language,
                                    "consistency_report": draft.consistency_report,
                                    "status": draft.status,
                                    "approved_at": (
                                        draft.approved_at.isoformat()
                                        if draft.approved_at is not None
                                        else None
                                    ),
                                    "approved_by": draft.approved_by,
                                }
                                if draft is not None
                                else None
                            ),
                        }
                    source_items.append(
                        {
                            "source_id": str(source.source_id),
                            "suggestion_id": str(source.suggestion_id),
                            "party_id": str(source.party_id),
                            "party_order": party_order.get(source.party_id),
                            "party_role": (
                                party.role if party is not None else "UNKNOWN"
                            ),
                            "party_display_name": (
                                party.display_name
                                if party is not None
                                else "未知来源"
                            ),
                            "excerpt": source.excerpt,
                            "localized_claim": source.localized_claim,
                            "span_refs": source.span_refs,
                            "status": source.status,
                            "reply": reply_item,
                        }
                    )

            # 排序与派生字段在 session 内完成，避免延迟加载
            source_items.sort(key=_source_order)
            suggestion_by_id = {
                item["suggestion_id"]: item for item in suggestion_items
            }
            internal_revision_items: list[dict[str, Any]] = []
            for suggestion in suggestion_items:
                related_sources = [
                    source
                    for source in source_items
                    if source["suggestion_id"] == suggestion["suggestion_id"]
                ]
                internal_revision_items.append(
                    {
                        "suggestion_id": suggestion["suggestion_id"],
                        "canonical_text": suggestion["canonical_text"],
                        "priority": suggestion["priority"],
                        "modification_facts": [
                            fact["paper_change_summary"]
                            for fact in suggestion["modification_facts"]
                            if fact["status"] == "CONFIRMED"
                        ],
                        "source_ids": [
                            source["source_id"] for source in related_sources
                        ],
                        "source_labels": [
                            source["party_display_name"] for source in related_sources
                        ],
                    }
                )

            external_replies: list[dict[str, Any]] = []
            for source in source_items:
                reply = source.get("reply")
                draft = (
                    reply.get("current_draft") if isinstance(reply, dict) else None
                )
                external_replies.append(
                    {
                        "source_id": source["source_id"],
                        "suggestion_id": source["suggestion_id"],
                        "party_id": source["party_id"],
                        "party_role": source["party_role"],
                        "party_display_name": source["party_display_name"],
                        "excerpt": source["excerpt"],
                        "localized_claim": source["localized_claim"],
                        "reply_status": (
                            reply.get("status") if isinstance(reply, dict) else None
                        ),
                        "draft_id": (
                            draft.get("draft_id") if isinstance(draft, dict) else None
                        ),
                        "draft_status": (
                            draft.get("status") if isinstance(draft, dict) else None
                        ),
                        "content": (
                            draft.get("content", "") if isinstance(draft, dict) else ""
                        ),
                    }
                )

            return {
                "workspace_id": str(workspace_id),
                "workspace_title": workspace.title,
                "user_id": workspace.user_id,
                "global_settings": dict(workspace.global_settings or {}),
                "suggestions": suggestion_items,
                "sources": source_items,
                "internal_revision_items": internal_revision_items,
                "external_replies": external_replies,
                "suggestion_by_id": suggestion_by_id,
            }

    def load_export_snapshot(self, snapshot_id: UUID) -> dict[str, Any] | None:
        audit_repo = AuditRepository()
        with session_scope(self._session_factory) as session:
            existing = audit_repo.list_by_target(
                session, target_type="EXPORT", target_id=snapshot_id
            )
            if not existing:
                return None
            payload = existing[-1].payload
            return dict(payload) if isinstance(payload, dict) else None

    def save_export_snapshot(
        self,
        *,
        workspace_id: UUID,
        snapshot_id: UUID,
        snapshot: dict[str, Any],
        actor_user_id: str,
    ) -> dict[str, Any]:
        audit_repo = AuditRepository()
        with session_scope(self._session_factory) as session:
            existing = audit_repo.list_by_target(
                session, target_type="EXPORT", target_id=snapshot_id
            )
            if existing:
                payload = existing[-1].payload
                return dict(payload) if isinstance(payload, dict) else dict(snapshot)
            audit_repo.create(
                session,
                workspace_id=workspace_id,
                target_type="EXPORT",
                target_id=snapshot_id,
                action="CONFIRM",
                payload=snapshot,
                actor_user_id=actor_user_id,
            )
            return dict(snapshot)

    def load_latest_export_snapshot(
        self, workspace_id: UUID
    ) -> dict[str, Any] | None:
        audit_repo = AuditRepository()
        with session_scope(self._session_factory) as session:
            export_events = [
                event
                for event in audit_repo.list_by_workspace(session, workspace_id)
                if event.target_type == "EXPORT" and event.action == "CONFIRM"
            ]
            if not export_events:
                return None
            payload = export_events[-1].payload
            return dict(payload) if isinstance(payload, dict) else None


def _source_order(source: dict[str, Any]) -> tuple[int, int, str]:
    """与 backend finalize_graph._source_order 一致。"""
    span_refs = source.get("span_refs")
    order = span_refs.get("source_order") if isinstance(span_refs, dict) else None
    try:
        numeric_order = int(order)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        numeric_order = 2**31 - 1
    party_order = source.get("party_order")
    try:
        party_ord = int(party_order)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        party_ord = 2**31 - 1
    return (party_ord, numeric_order, str(source["source_id"]))


class PostgresRunStore(_SessionStoreBase):
    """RunStore 能力（A4 可选端口；按 GraphRunRepository 实现，供 Wave2/C1 使用）。"""

    def get_graph_run(self, run_id: UUID) -> GraphRunRecord | None:
        repo = GraphRunRepository()
        with session_scope(self._session_factory) as session:
            entity = repo.get_by_id(session, run_id)
            if entity is None:
                return None
            return _graph_run_record(entity)

    def mark_running(
        self, run_id: UUID, *, started_at: datetime | None = None
    ) -> GraphRunRecord | None:
        return self._update(
            run_id,
            status="RUNNING",
            started_at=started_at or datetime.now(timezone.utc),
        )

    def mark_waiting(
        self,
        run_id: UUID,
        *,
        result_refs: list[dict[str, Any]] | list[Any] | None = None,
    ) -> GraphRunRecord | None:
        return self._update(
            run_id, status="WAITING_USER", result_refs=result_refs
        )

    def mark_succeeded(
        self,
        run_id: UUID,
        *,
        result_refs: list[dict[str, Any]] | list[Any] | None = None,
        finished_at: datetime | None = None,
    ) -> GraphRunRecord | None:
        return self._update(
            run_id,
            status="SUCCEEDED",
            result_refs=result_refs,
            finished_at=finished_at or datetime.now(timezone.utc),
        )

    def mark_failed(
        self,
        run_id: UUID,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        final: bool = False,
        finished_at: datetime | None = None,
    ) -> GraphRunRecord | None:
        return self._update(
            run_id,
            status="FAILED_FINAL" if final else "FAILED_RETRYABLE",
            error_code=error_code,
            error_message=error_message,
            finished_at=finished_at or datetime.now(timezone.utc),
        )

    def _update(
        self,
        run_id: UUID,
        *,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        result_refs: list[dict[str, Any]] | list[Any] | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> GraphRunRecord | None:
        repo = GraphRunRepository()
        with session_scope(self._session_factory) as session:
            entity = repo.update_status(
                session,
                run_id,
                status=status,
                error_code=error_code,
                error_message=error_message,
                result_refs=result_refs,
                started_at=started_at,
                finished_at=finished_at,
            )
            if entity is None:
                return None
            return _graph_run_record(entity)


def build_postgres_stores(
    session_factory: sessionmaker[Session] | None = None,
) -> dict[str, Any]:
    """构造全套默认 Postgres stores，便于注入图工厂。"""
    factory = (
        session_factory if session_factory is not None else create_session_factory()
    )
    return {
        "workspace": PostgresWorkspaceStore(factory),
        "suggestion": PostgresSuggestionStore(factory),
        "analysis": PostgresAnalysisStore(factory),
        "reply": PostgresReplyStore(factory),
        "manuscript": PostgresManuscriptStore(factory),
        "run": PostgresRunStore(factory),
        "finalize": PostgresFinalizeStore(factory),
    }
