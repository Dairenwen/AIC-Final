from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class LiteratureRuntimeContext:
    user_id: str
    thread_id: str


class ConversationContext(BaseModel):
    turn_id: UUID
    user_text: str
    intent_summary: str
    start_year: int
    end_year: int
    top_papers: list[dict[str, Any]] = Field(default_factory=list)
