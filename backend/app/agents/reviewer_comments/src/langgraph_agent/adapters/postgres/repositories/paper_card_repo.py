"""PaperCardRecord 数据访问。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from langgraph_agent.adapters.postgres.models import PaperCardRecord
from langgraph_agent.adapters.postgres.base import add_and_flush, get_by_pk

# 清单 24 只读消费「已确认基线」的合法状态集合（与 ConfirmationStatus 对齐）。
_CONFIRMED_STATUSES = ("CONFIRMED", "EDITED")


class PaperCardRepository:
    """论文信息卡片的创建、查询与确认状态更新。"""

    def create(
        self,
        session: Session,
        *,
        workspace_id: uuid.UUID,
        manuscript_version_id: uuid.UUID,
        card_type: str,
        content: str,
        source_sections: list[Any],
        source_quote: str,
        confidence: float,
        confirmation_status: str,
    ) -> PaperCardRecord:
        card = PaperCardRecord(
            workspace_id=workspace_id,
            manuscript_version_id=manuscript_version_id,
            card_type=card_type,
            content=content,
            source_sections=source_sections,
            source_quote=source_quote,
            confidence=confidence,
            confirmation_status=confirmation_status,
        )
        return add_and_flush(session, card)

    def bulk_create(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        manuscript_version_id: uuid.UUID,
        cards: list[dict],
    ) -> list[PaperCardRecord]:
        """批量插入卡片；每项 dict 键对齐 PaperCard.to_dict()。"""
        created: list[PaperCardRecord] = []
        for item in cards:
            card = PaperCardRecord(
                workspace_id=workspace_id,
                manuscript_version_id=manuscript_version_id,
                card_type=item["card_type"],
                content=item["content"],
                source_sections=item["source_sections"],
                source_quote=item["source_quote"],
                confidence=item["confidence"],
                confirmation_status=item["confirmation_status"],
            )
            session.add(card)
            created.append(card)
        session.flush()
        return created

    def list_by_manuscript(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        manuscript_version_id: uuid.UUID,
    ) -> list[PaperCardRecord]:
        statement = (
            select(PaperCardRecord)
            .where(
                PaperCardRecord.workspace_id == workspace_id,
                PaperCardRecord.manuscript_version_id == manuscript_version_id,
            )
            .order_by(PaperCardRecord.created_at, PaperCardRecord.paper_card_id)
        )
        return list(session.scalars(statement))

    def delete_by_manuscript(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        manuscript_version_id: uuid.UUID,
    ) -> int:
        """删除该版本已有候选卡片，供幂等后台任务替换结果。"""
        result = session.execute(
            delete(PaperCardRecord).where(
                PaperCardRecord.workspace_id == workspace_id,
                PaperCardRecord.manuscript_version_id == manuscript_version_id,
            )
        )
        return result.rowcount or 0

    def list_confirmed(
        self,
        session: Session,
        workspace_id: uuid.UUID,
        manuscript_version_id: uuid.UUID,
    ) -> list[PaperCardRecord]:
        """返回 confirmation_status ∈ {CONFIRMED, EDITED} 的卡片。"""
        statement = (
            select(PaperCardRecord)
            .where(
                PaperCardRecord.workspace_id == workspace_id,
                PaperCardRecord.manuscript_version_id == manuscript_version_id,
                PaperCardRecord.confirmation_status.in_(_CONFIRMED_STATUSES),
            )
            .order_by(PaperCardRecord.created_at, PaperCardRecord.paper_card_id)
        )
        return list(session.scalars(statement))

    def update_confirmation(
        self,
        session: Session,
        paper_card_id: uuid.UUID,
        *,
        confirmation_status: str,
        content: str | None = None,
    ) -> PaperCardRecord | None:
        card = get_by_pk(session, PaperCardRecord, paper_card_id)
        if card is None:
            return None
        card.confirmation_status = confirmation_status
        if content is not None:
            card.content = content
        session.flush()
        return card
