from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from schemas.models import KnowledgeChunk, ReadingResult


class ContractViolationError(ValueError):
    code = "CONTRACT_VIOLATION"
    public_message = "The reading result failed contract integrity validation."


def _require_unique(values: list[str], label: str) -> None:
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        raise ContractViolationError(f"duplicate {label}: {', '.join(duplicates)}")


def validate_loaded_chunks_integrity(
    *,
    expected_paper_ids: set[str],
    loaded_chunks: Sequence[KnowledgeChunk],
) -> None:
    if not expected_paper_ids:
        raise ContractViolationError("expected paper scope is empty")
    if not loaded_chunks:
        raise ContractViolationError("no chunks were loaded")
    _require_unique([chunk.chunk_id for chunk in loaded_chunks], "chunk_id")
    for chunk in loaded_chunks:
        if chunk.paper_id not in expected_paper_ids:
            raise ContractViolationError("loaded chunk is outside the requested paper scope")


def validate_reading_result_integrity(
    *,
    result: ReadingResult,
    expected_paper_ids: set[str],
    loaded_chunks: Sequence[KnowledgeChunk],
) -> None:
    validate_loaded_chunks_integrity(
        expected_paper_ids=expected_paper_ids,
        loaded_chunks=loaded_chunks,
    )
    if {result.paper_id} != expected_paper_ids:
        raise ContractViolationError("result paper scope does not match the request")

    chunk_by_id = {chunk.chunk_id: chunk for chunk in loaded_chunks}
    _require_unique([item.evidence_id for item in result.evidence], "evidence_id")
    _require_unique([item.claim_id for item in result.claims], "claim_id")
    evidence_by_id = {item.evidence_id: item for item in result.evidence}
    claim_by_id = {item.claim_id: item for item in result.claims}

    for claim in result.claims:
        if not claim.evidence_ids:
            raise ContractViolationError("claim has no evidence reference")
        for evidence_id in claim.evidence_ids:
            if evidence_id not in evidence_by_id:
                raise ContractViolationError("claim references missing evidence")

    for evidence in result.evidence:
        chunk = chunk_by_id.get(evidence.object_id)
        if chunk is None:
            raise ContractViolationError("evidence references a missing chunk")
        if not evidence.evidence_text.strip():
            raise ContractViolationError("evidence text is empty")
        if evidence.paper_id != result.paper_id or chunk.paper_id != result.paper_id:
            raise ContractViolationError("evidence, chunk, and result paper identities differ")

    section_ids = (
        result.research_questions
        + result.method_structure
        + result.key_equations_and_figures
        + result.experiment_findings
        + result.innovations
        + result.limitations
    )
    for claim_id in section_ids:
        if claim_id not in claim_by_id:
            raise ContractViolationError("result section references a missing claim")
