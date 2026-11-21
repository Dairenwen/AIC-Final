from __future__ import annotations

from typing import Protocol

from schemas.models import (
    ArtifactReference,
    KnowledgeChunk,
    LocalPaperIdentity,
    LocalPaperImport,
    PaperRecord,
    PaperWorkspace,
    ReadingResult,
    ReadingRunStatus,
    ReadingSourceContext,
)


class KnowledgeBasePort(Protocol):
    def get_paper(self, paper_id: str) -> PaperRecord: ...

    def get_chunks(self, paper_id: str) -> list[KnowledgeChunk]: ...


class ArtifactStorePort(Protocol):
    def save_reading_result(
        self,
        reading_run_id: str,
        result: ReadingResult,
        source_context: ReadingSourceContext | None,
    ) -> ArtifactReference: ...


class RunRepositoryPort(Protocol):
    def add(self, run: ReadingRunStatus) -> None: ...

    def get(self, reading_run_id: str) -> ReadingRunStatus | None: ...

    def update(self, run: ReadingRunStatus) -> None: ...


class PaperWorkspaceRepositoryPort(Protocol):
    def add_import(self, record: LocalPaperImport) -> None: ...

    def update_import(self, record: LocalPaperImport) -> None: ...

    def add_identity_workspace_and_complete_import(
        self,
        identity: LocalPaperIdentity,
        workspace: PaperWorkspace,
        record: LocalPaperImport,
    ) -> None: ...

    def get_import(self, import_id: str) -> LocalPaperImport | None: ...

    def get_identity(self, paper_id: str) -> LocalPaperIdentity | None: ...

    def get_identity_by_hash(self, content_sha256: str) -> LocalPaperIdentity | None: ...

    def get_workspace(self, workspace_id: str) -> PaperWorkspace | None: ...

    def get_workspace_for_paper(self, paper_id: str) -> PaperWorkspace | None: ...


class PaperWorkspaceStorePort(Protocol):
    def save_source_pdf(self, workspace_id: str, content: bytes) -> str: ...

    def remove_workspace(self, workspace_id: str) -> None: ...
