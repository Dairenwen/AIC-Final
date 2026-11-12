"""审稿方与原始条目的规则抽取。"""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import Field

from langgraph_agent.schemas import ApiSchema


_ITEM_MARKER = re.compile(
    r"^\s*(?:(?:comment|point|意见|问题)\s*)?"
    r"(?P<number>\d+)[\.\):：、]\s*(?P<text>.+)$",
    re.IGNORECASE,
)
_BULLET_MARKER = re.compile(r"^\s*[-*•]\s+(?P<text>.+)$")


class LoadedReviewInput(ApiSchema):
    """从存储读取并序列化后的当前审稿输入。"""

    review_input_id: UUID
    party_id: UUID
    role: str
    display_name: str
    raw_label: str
    raw_text: str
    language: str | None = None


class ExtractedReviewParty(ApiSchema):
    """本轮输入中识别出的审稿方。"""

    party_id: UUID
    role: str
    display_name: str
    raw_label: str


class OriginalReviewItem(ApiSchema):
    """可独立进入拆分节点的一条原始审稿条目。"""

    original_item_id: str
    original_item_number: str | None
    review_input_id: UUID
    party_id: UUID
    role: str
    display_name: str
    original_text: str
    language: str | None = None
    source_order: int = Field(ge=1)


class ExtractPartiesAndItemsResult(ApiSchema):
    """审稿方与原始条目抽取结果。"""

    parties: list[ExtractedReviewParty]
    items: list[OriginalReviewItem]


def _split_numbered_items(raw_text: str) -> list[tuple[str | None, str]]:
    """按编号或项目符号切分；没有显式标记时按段落切分。"""
    items: list[tuple[str | None, str]] = []
    current_number: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        if not current_lines:
            return
        text = "\n".join(current_lines).strip()
        if text:
            items.append((current_number, text))

    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            if current_lines:
                current_lines.append("")
            continue

        numbered = _ITEM_MARKER.match(line)
        bullet = _BULLET_MARKER.match(line)
        if numbered or bullet:
            flush()
            current_lines = []
            if numbered:
                current_number = numbered.group("number")
                current_lines.append(numbered.group("text").strip())
            else:
                current_number = None
                current_lines.append(bullet.group("text").strip())
            continue
        current_lines.append(stripped)
    flush()

    if len(items) == 1 and items[0][0] is None:
        paragraphs = [
            re.sub(r"\s+", " ", paragraph).strip()
            for paragraph in re.split(r"\n\s*\n", raw_text)
            if paragraph.strip()
        ]
        if len(paragraphs) > 1:
            return [(None, paragraph) for paragraph in paragraphs]
    return items


def extract_parties_and_items(
    review_inputs: list[LoadedReviewInput | dict[str, object]],
) -> ExtractPartiesAndItemsResult:
    """从当前 ReviewInput 识别审稿方，并切出有序原始条目。"""
    loaded = [
        item
        if isinstance(item, LoadedReviewInput)
        else LoadedReviewInput.model_validate(item)
        for item in review_inputs
    ]
    parties: list[ExtractedReviewParty] = []
    seen_party_ids: set[UUID] = set()
    items: list[OriginalReviewItem] = []

    for review_input in loaded:
        if review_input.party_id not in seen_party_ids:
            parties.append(
                ExtractedReviewParty(
                    party_id=review_input.party_id,
                    role=review_input.role,
                    display_name=review_input.display_name,
                    raw_label=review_input.raw_label,
                )
            )
            seen_party_ids.add(review_input.party_id)

        split_items = _split_numbered_items(review_input.raw_text)
        if not split_items:
            raise ValueError(
                f"审稿输入 {review_input.review_input_id} 没有可处理的文本条目"
            )
        for source_order, (number, original_text) in enumerate(
            split_items, start=1
        ):
            items.append(
                OriginalReviewItem(
                    original_item_id=(
                        f"{review_input.review_input_id}:{source_order}"
                    ),
                    original_item_number=number,
                    review_input_id=review_input.review_input_id,
                    party_id=review_input.party_id,
                    role=review_input.role,
                    display_name=review_input.display_name,
                    original_text=original_text,
                    language=review_input.language,
                    source_order=source_order,
                )
            )

    return ExtractPartiesAndItemsResult(parties=parties, items=items)
