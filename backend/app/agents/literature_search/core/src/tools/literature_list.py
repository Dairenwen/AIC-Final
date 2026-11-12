from __future__ import annotations

import re
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from src.schemas import LiteratureListItem


TITLE_KEYS = ("标题名", "标题", "title", "paper_title", "name")
YEAR_KEYS = ("年份", "year", "published_year", "publication_year", "publicationYear")
VENUE_KEYS = ("会议", "venue", "conference", "journal", "publication", "booktitle")


class LiteratureListInput(BaseModel):
    papers: list[dict[str, Any]] = Field(description="Ranked and deduplicated papers from step 3")
    max_items: int | None = Field(default=None, ge=1, description="Optional maximum number of papers")


class LiteratureListTool(BaseTool):
    name: str = "format_literature_list"
    description: str = (
        "Convert ranked paper JSON into a display list, renumbering items from 1 to N "
        "and retaining title, year, and conference or venue."
    )
    args_schema: type[BaseModel] = LiteratureListInput

    def _run(self, papers: list[dict[str, Any]], max_items: int | None = None) -> dict[str, Any]:
        selected = papers[:max_items] if max_items is not None else papers
        items = [normalize_paper(paper, index) for index, paper in enumerate(selected, start=1)]
        return {
            "total": len(items),
            "literature_list": [item.to_display_dict() for item in items],
        }


def normalize_paper(paper: dict[str, Any], sequence: int) -> LiteratureListItem:
    title = string_value(first_value(paper, TITLE_KEYS))
    if not title:
        raise ValueError(f"Paper {sequence} is missing a title")
    return LiteratureListItem(
        sequence=sequence,
        title=title,
        year=parse_year(first_value(paper, YEAR_KEYS)),
        venue=string_value(first_value(paper, VENUE_KEYS)) or "未知",
    )


def first_value(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return value
    return None


def string_value(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip() if value is not None else ""


def parse_year(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and 1000 <= value <= 9999:
        return value
    match = re.search(r"\b(?:19|20)\d{2}\b", str(value))
    return int(match.group(0)) if match else None
