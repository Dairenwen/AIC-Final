from __future__ import annotations

from hashlib import sha256
from typing import Any

from llm.gateway import ModelGateway, ReadingAnalysis
from schemas.models import EvidenceReference, ReadingClaim, ReadingResult
from tools.ports import ArtifactStorePort, KnowledgeBasePort
from utils.contract_validation import ResultContractError, validate_reading_result_contract
from utils.result_integrity import (
    ContractViolationError,
    validate_loaded_chunks_integrity,
    validate_reading_result_integrity,
)

from .state import ReadingState


class MultiNotImplementedError(ValueError):
    code = "MULTI_NOT_IMPLEMENTED"
    feature_status = "planned_in_later_v0_1_phase"


SECTION_BY_CLAIM_TYPE = {
    "RESEARCH_QUESTION": "research_questions",
    "METHOD": "method_structure",
    "EQUATION_FIGURE": "key_equations_and_figures",
    "EXPERIMENT": "experiment_findings",
    "INNOVATION": "innovations",
    "LIMITATION": "limitations",
}


class ReadingWorkflowNodes:
    """Nodes shared by the direct Agent flow and the preserved legacy wrapper."""

    def __init__(self, model_gateway: ModelGateway) -> None:
        self.model_gateway = model_gateway

    def validate_request(self, state: ReadingState) -> dict[str, Any]:
        request = state["request"]
        if request.mode == "MULTI":
            raise MultiNotImplementedError("MULTI is planned in a later V0.1 phase")
        if len(request.paper_sources) != 1:
            raise ValueError("SINGLE mode requires exactly one paper")
        return {"node_trace": ["validate_request"]}

    def validate_source(self, state: ReadingState) -> dict[str, Any]:
        request_paper_id = state["request"].paper_sources[0].paper_id
        paper = state["paper"]
        if paper.paper_id != request_paper_id:
            raise ContractViolationError("paper metadata is outside the requested paper scope")
        validate_loaded_chunks_integrity(
            expected_paper_ids={request_paper_id},
            loaded_chunks=state["chunks"],
        )
        for chunk in state["chunks"]:
            if chunk.page is None or chunk.section is None or chunk.content_type is None:
                raise ContractViolationError("Agent flow requires located evidence chunks")
        return {"node_trace": ["validate_source"]}

    def prepare_context(self, state: ReadingState) -> dict[str, Any]:
        context = [chunk.model_dump(mode="json") for chunk in state["chunks"]]
        return {"context": context, "node_trace": ["prepare_context"]}

    def analyze_paper(self, state: ReadingState) -> dict[str, Any]:
        analysis = self.model_gateway.analyze_paper(
            state["request"],
            state["paper"],
            state["context"],
        )
        if not isinstance(analysis, ReadingAnalysis):
            analysis = ReadingAnalysis.model_validate(analysis)
        return {"analysis": analysis, "node_trace": ["analyze_paper"]}

    def bind_evidence(self, state: ReadingState) -> dict[str, Any]:
        chunk_by_id = {chunk.chunk_id: chunk for chunk in state["chunks"]}
        claims: list[ReadingClaim] = []
        evidence: list[EvidenceReference] = []
        evidence_id_by_chunk: dict[str, str] = {}
        sections: dict[str, list[str]] = {name: [] for name in SECTION_BY_CLAIM_TYPE.values()}
        used_claim_ids: set[str] = set()

        for candidate in state["analysis"].claims:
            claim_evidence_ids: list[str] = []
            for chunk_id in candidate.chunk_ids:
                try:
                    chunk = chunk_by_id[chunk_id]
                except KeyError as exc:
                    raise ContractViolationError("analysis references a missing chunk") from exc
                evidence_id = evidence_id_by_chunk.get(chunk_id)
                if evidence_id is None:
                    evidence_id = f"evidence_{len(evidence_id_by_chunk) + 1:03d}"
                    evidence_id_by_chunk[chunk_id] = evidence_id
                    evidence.append(
                        EvidenceReference(
                            evidence_id=evidence_id,
                            paper_id=chunk.paper_id,
                            evidence_type=chunk.content_type,
                            page_number=chunk.page,
                            section_path=chunk.section,
                            object_id=chunk.chunk_id,
                            evidence_text=chunk.text,
                            content_sha256=sha256(chunk.text.encode("utf-8")).hexdigest(),
                        )
                    )
                if evidence_id not in claim_evidence_ids:
                    claim_evidence_ids.append(evidence_id)

            candidate_claim_id = candidate.claim_id.strip()
            if not 3 <= len(candidate_claim_id) <= 128 or candidate_claim_id in used_claim_ids:
                candidate_claim_id = f"claim_{len(claims) + 1:03d}"
                while candidate_claim_id in used_claim_ids:
                    candidate_claim_id = f"claim_{len(claims) + len(used_claim_ids) + 1:03d}"
            used_claim_ids.add(candidate_claim_id)
            claim = ReadingClaim(
                claim_id=candidate_claim_id,
                claim_type=candidate.claim_type,
                claim_source=candidate.claim_source,
                content=candidate.content,
                evidence_ids=claim_evidence_ids,
            )
            claims.append(claim)
            sections[SECTION_BY_CLAIM_TYPE[claim.claim_type]].append(claim.claim_id)

        warnings = list(state["analysis"].warnings)
        for section_name, claim_ids in sections.items():
            if not claim_ids:
                warnings.append(
                    {
                        "warning_code": f"NO_{section_name.upper()}_EVIDENCE",
                        "message": f"No evidence-grounded claim was produced for {section_name}.",
                    }
                )

        request = state["request"]
        paper = state["paper"]
        analysis_information = state["analysis"].basic_information
        basic_information = (
            analysis_information.model_dump(mode="json")
            if analysis_information is not None
            else {"title": paper.title, "authors": paper.authors, "year": paper.year}
        )
        result = ReadingResult(
            result_id=f"result_{state['reading_run_id']}",
            request_id=request.request_id,
            paper_id=paper.paper_id,
            basic_information=basic_information,
            claims=claims,
            evidence=evidence,
            warnings=warnings,
            output_version={"contract_version": "reading_result_v1", "revision": 1},
            **sections,
        )
        return {"result": result, "node_trace": ["bind_evidence"]}

    def validate_result(self, state: ReadingState) -> dict[str, Any]:
        try:
            validate_reading_result_contract(state["result"].model_dump(mode="json"))
        except ResultContractError as exc:
            raise ContractViolationError("reading result failed JSON Schema validation") from exc
        validate_reading_result_integrity(
            result=state["result"],
            expected_paper_ids={source.paper_id for source in state["request"].paper_sources},
            loaded_chunks=state["chunks"],
        )
        return {"node_trace": ["validate_result"]}

    def complete(self, state: ReadingState) -> dict[str, Any]:
        return {"status": "SUCCEEDED", "node_trace": ["complete"]}


class ReadingNodes(ReadingWorkflowNodes):
    def __init__(
        self,
        knowledge_base: KnowledgeBasePort,
        model_gateway: ModelGateway,
        artifact_store: ArtifactStorePort,
    ) -> None:
        super().__init__(model_gateway)
        self.knowledge_base = knowledge_base
        self.artifact_store = artifact_store

    def load_paper(self, state: ReadingState) -> dict[str, Any]:
        paper_id = state["request"].paper_sources[0].paper_id
        return {"paper": self.knowledge_base.get_paper(paper_id), "node_trace": ["load_paper"]}

    def load_chunks(self, state: ReadingState) -> dict[str, Any]:
        chunks = self.knowledge_base.get_chunks(state["paper"].paper_id)
        validate_loaded_chunks_integrity(
            expected_paper_ids={source.paper_id for source in state["request"].paper_sources},
            loaded_chunks=chunks,
        )
        return {"chunks": chunks, "node_trace": ["load_chunks"]}

    def persist_artifact(self, state: ReadingState) -> dict[str, Any]:
        artifact = self.artifact_store.save_reading_result(
            state["reading_run_id"], state["result"], state.get("source_context")
        )
        return {"artifact": artifact, "node_trace": ["persist_artifact"]}
