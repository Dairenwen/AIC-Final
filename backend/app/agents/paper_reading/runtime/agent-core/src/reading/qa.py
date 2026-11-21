from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from llm.qa import QuestionAnsweringGateway
from schemas.models import EvidenceReference, QAResponse
from utils.contract_validation import validate_contract_payload

from .errors import ReadingStageError
from .planning import ReadingTaskType

if TYPE_CHECKING:
    from .context import PreparedReadingContext


@dataclass(frozen=True)
class PaperQaOutput:
    response: QAResponse
    evidence: tuple[EvidenceReference, ...]
    markdown: str


class PaperScopedQaAgent:
    def __init__(self, gateway: QuestionAnsweringGateway) -> None:
        self.gateway = gateway

    def ask(
        self,
        reading: "PreparedReadingContext",
        question: str,
        *,
        page_numbers: set[int] | None = None,
        section_path: list[str] | None = None,
        chunk_ids: set[str] | None = None,
        object_ids: set[str] | None = None,
        selected_text: str | None = None,
    ) -> PaperQaOutput:
        question = question.strip()
        if not question:
            raise ReadingStageError("QUESTION_REQUIRED")
        selected_text = selected_text.strip() if selected_text else None
        task_type = (
            ReadingTaskType.SELECTION_EXPLANATION
            if selected_text or object_ids
            else ReadingTaskType.PAPER_QA
        )
        scoped_chunks = list(
            reading.context_router.route(
                task_type,
                reading.request,
                reading.chunks,
                reading.document_ir,
                question=question,
                page_numbers=page_numbers,
                section_path=section_path,
                chunk_ids=chunk_ids,
                object_ids=object_ids,
                selected_text=selected_text,
            ).chunks
        )
        context = [chunk.model_dump(mode="json") for chunk in scoped_chunks]
        analysis = self.gateway.answer_question(
            question,
            reading.paper,
            context,
            reading.request.language,
        )
        chunk_by_id = {chunk.chunk_id: chunk for chunk in reading.chunks}
        evidence: list[EvidenceReference] = []
        for index, chunk_id in enumerate(dict.fromkeys(analysis.chunk_ids), start=1):
            try:
                chunk = chunk_by_id[chunk_id]
            except KeyError as exc:
                raise ReadingStageError("UNKNOWN_CHUNK_REFERENCE") from exc
            if chunk.page is None or chunk.section is None or chunk.content_type is None:
                raise ReadingStageError("LOCATED_CHUNK_REQUIRED")
            evidence_text = chunk.text[:2000]
            evidence.append(
                EvidenceReference(
                    evidence_id=f"evidence_qa_{index:03d}",
                    paper_id=reading.paper.paper_id,
                    evidence_type=chunk.content_type,
                    page_number=chunk.page,
                    section_path=chunk.section,
                    object_id=chunk.chunk_id,
                    evidence_text=evidence_text,
                    content_sha256=sha256(evidence_text.encode("utf-8")).hexdigest(),
                )
            )

        section_paths: list[list[str]] = []
        for item in evidence:
            if item.section_path not in section_paths:
                section_paths.append(item.section_path)
        qa_id = f"qa_{sha256(f'{reading.paper.paper_id}|{question}'.encode('utf-8')).hexdigest()[:24]}"
        response = QAResponse(
            qa_id=qa_id,
            request_id=reading.request.request_id,
            question=question,
            answer=analysis.answer,
            paper_scope={
                "paper_ids": [reading.paper.paper_id],
                "section_paths": section_paths,
            },
            evidence_ids=[item.evidence_id for item in evidence],
            answer_status=analysis.answer_status,
        )
        validate_contract_payload(response.model_dump(mode="json"), "qa_response")
        return PaperQaOutput(
            response=response,
            evidence=tuple(evidence),
            markdown=self._render(response, evidence),
        )

    def explain_selection(
        self,
        reading: "PreparedReadingContext",
        selected_text: str,
        *,
        page_number: int | None = None,
    ) -> PaperQaOutput:
        selected_text = selected_text.strip()
        if not selected_text:
            raise ReadingStageError("SELECTED_TEXT_REQUIRED")
        question = f"请结合论文上下文解释以下选中内容，并说明它在论文中的作用：{selected_text}"
        return self.ask(
            reading,
            question,
            page_numbers={page_number} if page_number else None,
            selected_text=selected_text,
        )

    def explain_object(
        self,
        reading: "PreparedReadingContext",
        object_id: str,
    ) -> PaperQaOutput:
        object_id = object_id.strip()
        if not object_id:
            raise ReadingStageError("SCIENTIFIC_OBJECT_NOT_FOUND")
        question = (
            "请结合论文上下文解释指定科学对象，说明其含义、作用和证据边界："
            f"{object_id}"
        )
        return self.ask(
            reading,
            question,
            object_ids={object_id},
        )

    @staticmethod
    def _render(response: QAResponse, evidence: list[EvidenceReference]) -> str:
        lines = ["# 论文问答", "", f"## 问题", "", response.question, "", "## 回答", "", response.answer]
        if evidence:
            lines.extend(("", "## 依据", ""))
            for item in evidence:
                section = " / ".join(item.section_path)
                lines.append(f"- p.{item.page_number} {section} ({item.object_id})")
        return "\n".join(lines).rstrip() + "\n"
