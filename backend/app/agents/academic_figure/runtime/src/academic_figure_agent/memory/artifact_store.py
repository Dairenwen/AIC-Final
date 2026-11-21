from __future__ import annotations

from pathlib import Path

from academic_figure_agent.schemas import ArtifactManifest


class ArtifactStore:
    """Read-only index over completed artifact manifests."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root

    def list_manifests(self) -> list[ArtifactManifest]:
        manifests: list[ArtifactManifest] = []
        if not self.output_root.exists():
            return manifests
        for path in sorted(self.output_root.glob("*/manifest.json"), reverse=True):
            try:
                manifests.append(ArtifactManifest.model_validate_json(path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                continue
        return manifests
