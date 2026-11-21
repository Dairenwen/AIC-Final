from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from llm.gateway import ModelGateway, ReadingAnalysis
from pydantic import ValidationError
from schemas.models import (
    KnowledgeChunk,
    PaperRecord,
    ReadingRequest,
    ReadingResult,
    ReadingRunError,
    ReadingRunStatus,
    ReadingSourceContext,
)
from tools.ports import ArtifactStorePort, KnowledgeBasePort, RunRepositoryPort

from .graph import build_agent_flow, build_reading_graph


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentFlowOutput:
    result: ReadingResult
    analysis: ReadingAnalysis
    node_trace: tuple[str, ...]


class PaperReadingAgent:
    """Primary core entry point: direct input, no HTTP, database, or artifact store."""

    def __init__(self, model_gateway: ModelGateway) -> None:
        self.graph = build_agent_flow(model_gateway)

    def run(
        self,
        request: ReadingRequest,
        paper: PaperRecord,
        chunks: list[KnowledgeChunk],
    ) -> AgentFlowOutput:
        final_state = self.graph.invoke(
            {
                "reading_run_id": f"agent_run_{uuid4().hex}",
                "request": request,
                "paper": paper,
                "chunks": chunks,
                "node_trace": [],
            }
        )
        return AgentFlowOutput(
            result=final_state["result"],
            analysis=final_state["analysis"],
            node_trace=tuple(final_state["node_trace"]),
        )


SAFE_ERROR_MESSAGES = {
    "CONTRACT_VIOLATION": "The reading result failed contract integrity validation.",
    "MULTI_NOT_IMPLEMENTED": "MULTI remains planned in a later V0.1 phase.",
    "INTERNAL_ERROR": "The reading run failed because of an internal error.",
}


class PaperReadingCoreAdapter:
    """The single backend-facing adapter for the agent-core workflow."""

    def __init__(
        self,
        knowledge_base: KnowledgeBasePort,
        model_gateway: ModelGateway,
        artifact_store: ArtifactStorePort,
        run_repository: RunRepositoryPort,
    ) -> None:
        self.run_repository = run_repository
        self.graph = build_reading_graph(knowledge_base, model_gateway, artifact_store)

    def create_and_execute(
        self,
        request: ReadingRequest,
        source_context: ReadingSourceContext | None = None,
    ) -> ReadingRunStatus:
        now = datetime.now(timezone.utc)
        reading_run_id = f"reading_run_{uuid4().hex}"
        run = ReadingRunStatus(
            reading_run_id=reading_run_id,
            request_id=request.request_id,
            status="PENDING",
            source_context=source_context,
            created_at=now,
            updated_at=now,
        )
        self.run_repository.add(run)
        run.status = "RUNNING"
        run.updated_at = datetime.now(timezone.utc)
        self.run_repository.update(run)
        try:
            final_state = self.graph.invoke(
                {
                    "reading_run_id": reading_run_id,
                    "request": request,
                    "source_context": source_context,
                    "node_trace": [],
                }
            )
            run.status = "SUCCEEDED"
            run.result = final_state["result"]
            run.artifact = final_state["artifact"]
            run.error = None
        except Exception as exc:
            error_code = (
                "CONTRACT_VIOLATION"
                if isinstance(exc, ValidationError)
                else getattr(exc, "code", "INTERNAL_ERROR")
            )
            if error_code not in SAFE_ERROR_MESSAGES:
                error_code = "INTERNAL_ERROR"
            logger.error("Reading run failed with error type=%s code=%s", type(exc).__name__, error_code)
            run.status = "FAILED"
            run.result = None
            run.artifact = None
            run.error = ReadingRunError(
                code=error_code,
                message=SAFE_ERROR_MESSAGES[error_code],
                feature_status=getattr(exc, "feature_status", None),
            )
        run.updated_at = datetime.now(timezone.utc)
        self.run_repository.update(run)
        return run.model_copy(deep=True)

    def get_run(self, reading_run_id: str) -> ReadingRunStatus | None:
        return self.run_repository.get(reading_run_id)
