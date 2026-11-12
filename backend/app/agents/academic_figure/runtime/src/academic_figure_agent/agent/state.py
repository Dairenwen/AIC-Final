from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from academic_figure_agent.schemas import (
    ArtifactManifest,
    CaptionSet,
    DatasetSummary,
    FigureRequest,
    FigureSpec,
    QualityReport,
)


class FigureAgentState(TypedDict, total=False):
    request: FigureRequest
    output_dir: str
    context: str
    dataset: DatasetSummary
    spec: FigureSpec
    code_files: dict[str, str]
    render_result: dict[str, object]
    captions: CaptionSet
    quality_report: QualityReport
    artifacts: ArtifactManifest
    revision: int
    warnings: Annotated[list[str], operator.add]
