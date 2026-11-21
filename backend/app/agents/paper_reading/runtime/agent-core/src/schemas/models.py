from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


SourceType = Literal["USER_UPLOAD", "ARXIV", "LITERATURE_RETRIEVAL_AGENT", "LOCAL_KNOWLEDGE_BASE"]
Id128 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=128)]
RequestId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^req_[a-z0-9_]+$")]
NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Text128 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Text256 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
Text1000 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Text4000 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
RelativeReference = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512, pattern=r"^[A-Za-z0-9._/-]+$"),
]


class PaperSource(ContractModel):
    paper_id: Id128
    source_type: SourceType
    source_uri: str | None


class ReadingRequest(ContractModel):
    schema_version: Literal["reading_request_v1"] = "reading_request_v1"
    request_id: RequestId
    mode: Literal["SINGLE", "MULTI"]
    depth: Literal["OVERVIEW", "STANDARD", "DEEP"]
    paper_sources: list[PaperSource] = Field(min_length=1, max_length=10)
    reading_goal: str = Field(min_length=1, max_length=1000)
    focus_aspects: list[
        Literal[
            "RESEARCH_QUESTION",
            "METHOD",
            "EQUATION",
            "FIGURE",
            "TABLE",
            "EXPERIMENT",
            "INNOVATION",
            "LIMITATION",
            "REPRODUCIBILITY",
        ]
    ] = Field(min_length=1)
    output_template: Literal["OVERVIEW_NOTE", "STANDARD_NOTE", "DEEP_NOTE", "COMPARISON_REPORT"]
    language: str


class PaperRecord(ContractModel):
    schema_version: Literal["paper_record_v1"] = "paper_record_v1"
    paper_id: Id128
    source_type: SourceType
    title: str
    authors: list[str] = Field(min_length=1)
    year: int | None
    arxiv_id: str | None
    doi: str | None
    source_uri: str | None
    version: str
    content_sha256: str | None
    ingest_status: Literal[
        "RECEIVED", "IMPORTED", "DUPLICATE", "METADATA_REVIEW", "UNSUPPORTED_DOCUMENT", "IMPORT_FAILED"
    ]


class LocatedObject(ContractModel):
    object_id: str
    page_number: int
    section_path: list[str]


class Page(LocatedObject):
    contained_object_ids: list[str]


class Section(LocatedObject):
    title: str


class TextBlock(LocatedObject):
    text: str


class LabeledObject(LocatedObject):
    label: str
    content: str


class ReferenceObject(LocatedObject):
    reference_key: str
    citation_text: str


class ParseQuality(ContractModel):
    status: Literal["PASS", "REVIEW", "FAILED"]
    text_coverage_ratio: float = Field(ge=0, le=1)
    warnings: list[str]


class DocumentIR(ContractModel):
    schema_version: Literal["document_ir_v1"] = "document_ir_v1"
    paper_id: Id128
    pages: list[Page] = Field(min_length=1)
    sections: list[Section] = Field(min_length=1)
    text_blocks: list[TextBlock]
    equations: list[LabeledObject]
    figures: list[LabeledObject]
    tables: list[LabeledObject]
    references: list[ReferenceObject]
    parse_quality: ParseQuality


class EvidenceReference(ContractModel):
    schema_version: Literal["evidence_reference_v1"] = "evidence_reference_v1"
    evidence_id: Id128
    paper_id: Id128
    evidence_type: Literal["TEXT", "EQUATION", "FIGURE", "TABLE", "CAPTION", "REFERENCE"]
    page_number: int
    section_path: list[str] = Field(min_length=1)
    object_id: str
    evidence_text: NonBlankText
    content_sha256: str


class ReadingClaim(ContractModel):
    schema_version: Literal["reading_claim_v1"] = "reading_claim_v1"
    claim_id: Id128
    claim_type: Literal[
        "RESEARCH_QUESTION",
        "METHOD",
        "EQUATION_FIGURE",
        "EXPERIMENT",
        "INNOVATION",
        "LIMITATION",
        "CONSENSUS",
        "DIFFERENCE",
        "CONFLICT",
        "RESEARCH_GAP",
    ]
    claim_source: Literal["AUTHOR_STATED", "EVIDENCE_DERIVED", "AGENT_INFERRED", "CROSS_PAPER_ASSESSED"]
    content: str
    evidence_ids: list[str] = Field(min_length=1)


class BasicInformation(ContractModel):
    title: str
    authors: list[str] = Field(min_length=1)
    year: int | None


class ReadingWarning(ContractModel):
    warning_code: str
    message: str


class OutputVersion(ContractModel):
    contract_version: Literal["reading_result_v1"] = "reading_result_v1"
    revision: int = Field(ge=1)


class ReadingResult(ContractModel):
    schema_version: Literal["reading_result_v1"] = "reading_result_v1"
    result_id: Id128
    request_id: RequestId
    paper_id: Id128
    basic_information: BasicInformation
    research_questions: list[str]
    method_structure: list[str]
    key_equations_and_figures: list[str]
    experiment_findings: list[str]
    innovations: list[str]
    limitations: list[str]
    claims: list[ReadingClaim] = Field(min_length=1)
    evidence: list[EvidenceReference] = Field(min_length=1)
    warnings: list[ReadingWarning]
    output_version: OutputVersion


class PaperScope(ContractModel):
    paper_ids: list[Id128] = Field(min_length=1, max_length=10)
    section_paths: list[list[str]]


class QAResponse(ContractModel):
    schema_version: Literal["qa_response_v1"] = "qa_response_v1"
    qa_id: str
    request_id: RequestId
    question: str
    answer: str
    paper_scope: PaperScope
    evidence_ids: list[str]
    answer_status: Literal["ANSWERED", "INSUFFICIENT_EVIDENCE", "OUT_OF_SCOPE"]


class MultiPaperComparison(ContractModel):
    """Typed boundary for the frozen contract; the workflow does not implement it in this phase."""

    schema_version: Literal["comparison_report_v1"] = "comparison_report_v1"
    comparison_id: str
    request_id: RequestId
    paper_ids: list[Id128] = Field(min_length=2, max_length=10)
    source_results: list[dict[str, Any]]
    paper_cards: list[dict[str, Any]]
    comparability_checks: list[dict[str, Any]]
    method_comparison_claim_ids: list[str]
    experiment_comparison_claim_ids: list[str]
    claims: list[ReadingClaim]
    synthesis: dict[str, list[str]]
    output_version: dict[str, Any]


class SelectedLiteratureResult(ContractModel):
    result_id: Id128
    paper_id: Id128 | None
    source_paper_id: Text256
    rank: int = Field(ge=1)
    selection_reason: Text1000
    availability_status: Literal["READY_FOR_READING", "METADATA_ONLY", "CONTENT_UNAVAILABLE"]

    @model_validator(mode="after")
    def ready_result_has_paper_id(self) -> "SelectedLiteratureResult":
        if self.availability_status == "READY_FOR_READING" and self.paper_id is None:
            raise ValueError("READY_FOR_READING requires paper_id")
        return self


class LiteratureSearchHandoff(ContractModel):
    schema_version: Literal["literature_search_handoff_v1"] = "literature_search_handoff_v1"
    source_agent: Text128
    source_run_id: Id128
    original_query: Text4000
    selected_results: list[SelectedLiteratureResult] = Field(min_length=1, max_length=10)


class ReadingSourceContext(ContractModel):
    source_agent: Text128
    source_run_id: Id128
    original_query: Text4000
    selection_reason: Text1000
    source_result_id: Id128
    source_paper_id: Text256


class ArtifactReference(ContractModel):
    artifact_id: str
    media_type: Literal["application/json"] = "application/json"
    uri: str


class ReadingRunError(ContractModel):
    code: str
    message: str
    feature_status: str | None = None


class ReadingRunStatus(ContractModel):
    schema_version: Literal["reading_run_status_v1"] = "reading_run_status_v1"
    reading_run_id: Id128
    request_id: RequestId
    status: Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED"]
    source_context: ReadingSourceContext | None = None
    result: ReadingResult | None = None
    artifact: ArtifactReference | None = None
    error: ReadingRunError | None = None
    created_at: datetime
    updated_at: datetime


class KnowledgeChunk(ContractModel):
    chunk_id: Id128
    paper_id: Id128
    text: str
    page: int | None = None
    section: list[str] | None = None
    content_type: Literal["TEXT", "EQUATION", "FIGURE", "TABLE", "CAPTION", "REFERENCE"] | None = None
    chunk_set_id: Id128 | None = None
    document_object_ids: list[Id128] = Field(default_factory=list)
    source_start: int | None = Field(default=None, ge=0)
    source_end: int | None = Field(default=None, ge=1)
    splitter_strategy: Literal[
        "fixed_boundary_v1", "paragraph_sentence_v1", "section_parent_child_v1"
    ] | None = None

    @model_validator(mode="after")
    def source_range_is_ordered(self) -> "KnowledgeChunk":
        if (self.source_start is None) != (self.source_end is None):
            raise ValueError("source_start and source_end must be provided together")
        if self.source_start is not None and self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


class ReadingArtifactMetadata(ContractModel):
    reading_run_id: Id128
    request_id: RequestId
    paper_id: Id128
    source_context: ReadingSourceContext | None = None


class ReadingArtifact(ContractModel):
    schema_version: Literal["reading_artifact_v1"] = "reading_artifact_v1"
    metadata: ReadingArtifactMetadata
    result: ReadingResult


class LocalPaperImport(ContractModel):
    schema_version: Literal["local_paper_import_v1"] = "local_paper_import_v1"
    import_id: Annotated[str, StringConstraints(pattern=r"^import_[a-f0-9]{32}$")]
    source_kind: Literal["LOCAL_FILE"] = "LOCAL_FILE"
    source_reference: Annotated[str, StringConstraints(pattern=r"^upload:import_[a-f0-9]{32}$")]
    display_name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=255, pattern=r"^[^/\\\x00-\x1f]+$"),
    ]
    status: Literal["RECEIVED", "IMPORTED", "DUPLICATE", "UNSUPPORTED_DOCUMENT", "IMPORT_FAILED"]
    detected_media_type: Literal["application/pdf"] | None = None
    content_sha256: Sha256 | None = None
    paper_id: Annotated[str, StringConstraints(pattern=r"^paper_[a-f0-9]{64}$")] | None = None
    workspace_id: Annotated[str, StringConstraints(pattern=r"^workspace_[a-f0-9]{64}$")] | None = None
    warnings: list[Text256]
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def completed_import_has_identity(self) -> "LocalPaperImport":
        if self.status in {"IMPORTED", "DUPLICATE"}:
            if not all((self.detected_media_type, self.content_sha256, self.paper_id, self.workspace_id)):
                raise ValueError(f"{self.status} requires media type, hash, paper, and workspace identities")
        elif self.paper_id is not None or self.workspace_id is not None:
            raise ValueError(f"{self.status} must not bind a paper or workspace")
        return self


class LocalPaperIdentity(ContractModel):
    schema_version: Literal["local_paper_identity_v1"] = "local_paper_identity_v1"
    paper_id: Annotated[str, StringConstraints(pattern=r"^paper_[a-f0-9]{64}$")]
    content_sha256: Sha256
    duplicate_of_paper_id: Annotated[str, StringConstraints(pattern=r"^paper_[a-f0-9]{64}$")] | None = None
    identity_status: Literal["ACTIVE", "SUPERSEDED"]
    version: int = Field(ge=1)
    arxiv_id: Text128 | None = None
    doi: Text256 | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def duplicate_target_is_not_self(self) -> "LocalPaperIdentity":
        if self.duplicate_of_paper_id == self.paper_id:
            raise ValueError("duplicate_of_paper_id must not equal paper_id")
        return self


class PaperWorkspace(ContractModel):
    schema_version: Literal["paper_workspace_v1"] = "paper_workspace_v1"
    workspace_id: Annotated[str, StringConstraints(pattern=r"^workspace_[a-f0-9]{64}$")]
    paper_id: Annotated[str, StringConstraints(pattern=r"^paper_[a-f0-9]{64}$")]
    format_version: Literal["paper_workspace_v1"] = "paper_workspace_v1"
    source_pdf_ref: RelativeReference
    document_ir_ref: RelativeReference | None = None
    chunk_set_refs: list[RelativeReference]
    artifact_refs: list[RelativeReference]
    export_refs: list[RelativeReference]
    status: Literal["ACTIVE", "ERROR"]
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def references_are_relative_and_contained(self) -> "PaperWorkspace":
        references = [self.source_pdf_ref, *self.chunk_set_refs, *self.artifact_refs, *self.export_refs]
        if self.document_ir_ref is not None:
            references.append(self.document_ir_ref)
        if any(reference.startswith("/") or ".." in reference.split("/") for reference in references):
            raise ValueError("workspace references must be relative and contained")
        expected_source = f"workspaces/{self.workspace_id}/source/document.pdf"
        if self.source_pdf_ref != expected_source:
            raise ValueError("source_pdf_ref must belong to workspace_id")
        return self


class LocalPaperImportResult(ContractModel):
    schema_version: Literal["local_paper_import_result_v1"] = "local_paper_import_result_v1"
    import_record: LocalPaperImport
    paper_identity: LocalPaperIdentity
    workspace: PaperWorkspace

    @model_validator(mode="after")
    def records_share_identity(self) -> "LocalPaperImportResult":
        if self.import_record.paper_id != self.paper_identity.paper_id:
            raise ValueError("import and paper identity differ")
        if self.import_record.workspace_id != self.workspace.workspace_id:
            raise ValueError("import and workspace identity differ")
        if self.paper_identity.paper_id != self.workspace.paper_id:
            raise ValueError("paper and workspace identity differ")
        if self.import_record.content_sha256 != self.paper_identity.content_sha256:
            raise ValueError("import and paper content hashes differ")
        return self
