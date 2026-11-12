from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from schemas.models import (
    ArtifactReference,
    ReadingArtifact,
    ReadingArtifactMetadata,
    ReadingResult,
    ReadingSourceContext,
)


class LocalArtifactStore:
    """Stores runtime JSON below an injected ignored directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def save_reading_result(
        self,
        reading_run_id: str,
        result: ReadingResult,
        source_context: ReadingSourceContext | None,
    ) -> ArtifactReference:
        if Path(reading_run_id).name != reading_run_id or not reading_run_id.strip():
            raise ValueError("invalid reading run identifier")
        run_directory = self.root / reading_run_id
        run_directory.mkdir(parents=True, exist_ok=True)
        artifact_id = f"artifact_{reading_run_id}"
        destination = run_directory / "result.json"
        artifact = ReadingArtifact(
            metadata=ReadingArtifactMetadata(
                reading_run_id=reading_run_id,
                request_id=result.request_id,
                paper_id=result.paper_id,
                source_context=source_context,
            ),
            result=result,
        )
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=run_directory,
                prefix=".result.",
                suffix=".tmp",
                text=True,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(artifact.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, destination)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return ArtifactReference(
            artifact_id=artifact_id,
            media_type="application/json",
            uri=f"artifact:{reading_run_id}/result.json",
        )

    def load_reading_result(self, reading_run_id: str) -> ReadingArtifact:
        return ReadingArtifact.model_validate_json(
            (self.root / reading_run_id / "result.json").read_text(encoding="utf-8")
        )
