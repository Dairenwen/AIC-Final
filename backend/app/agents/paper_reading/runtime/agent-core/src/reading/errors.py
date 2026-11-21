from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .execution import DegradationCandidate, FlowDegradation


@dataclass(frozen=True)
class ErrorDescriptor:
    category: str
    message: str
    action: str


ERROR_DESCRIPTORS: dict[str, ErrorDescriptor] = {
    "QUESTION_REQUIRED": ErrorDescriptor(
        "USER_SCOPE",
        "A non-empty paper question is required.",
        "Provide a question and retry.",
    ),
    "SELECTED_TEXT_REQUIRED": ErrorDescriptor(
        "USER_SCOPE",
        "Non-empty selected text is required.",
        "Select text copied from the paper or use a scientific object ID.",
    ),
    "SELECTED_TEXT_NOT_FOUND": ErrorDescriptor(
        "USER_SCOPE",
        "The selected text was not found after safe text normalization.",
        "Choose one reported candidate or copy exact text from the extracted paper.",
    ),
    "SCIENTIFIC_OBJECT_NOT_FOUND": ErrorDescriptor(
        "USER_SCOPE",
        "The selected scientific object was not found in this paper.",
        "Use an object ID reported by the current DocumentIR.",
    ),
    "CHUNK_SCOPE_NOT_FOUND": ErrorDescriptor(
        "USER_SCOPE",
        "The selected Chunk was not found in this paper.",
        "Use a Chunk ID reported by the current reading result.",
    ),
    "QUESTION_SCOPE_EMPTY": ErrorDescriptor(
        "USER_SCOPE",
        "The requested scope contains no paper Chunks.",
        "Remove or broaden the page, section, Chunk, or object filter.",
    ),
    "UNKNOWN_CHUNK_REFERENCE": ErrorDescriptor(
        "RELIABILITY_GATE",
        "The model referenced a Chunk outside the current paper context.",
        "Retry with the same paper scope; do not accept the ungrounded answer.",
    ),
    "LOCATED_CHUNK_REQUIRED": ErrorDescriptor(
        "RELIABILITY_GATE",
        "The answer cited a Chunk without a valid page and section location.",
        "Repair the paper context lineage before retrying.",
    ),
    "VISION_TOOL_UNAVAILABLE": ErrorDescriptor(
        "EXTERNAL_TOOL",
        "A required visual-analysis tool is unavailable or incompatible.",
        "Configure a trusted Poppler/ImageMagick executable or disable visual analysis.",
    ),
    "VISION_RENDER_FAILED": ErrorDescriptor(
        "EXTERNAL_TOOL",
        "The configured visual tool could not render the requested paper object.",
        "Check the PDF and executable compatibility, then retry the visual stage.",
    ),
    "MODEL_OUTPUT_SCHEMA_INVALID": ErrorDescriptor(
        "MODEL",
        "The model response did not satisfy the required structured output.",
        "Retry once or select a model with reliable structured JSON output.",
    ),
    "MODEL_REQUEST_FAILED": ErrorDescriptor(
        "MODEL",
        "The configured model request failed.",
        "Check the endpoint, model availability, timeout, and rate limits.",
    ),
    "OPTIONAL_STAGE_FAILED": ErrorDescriptor(
        "INTERNAL",
        "An optional stage failed; the completed base report was preserved.",
        "Review the stage code and retry only the failed optional stage.",
    ),
}


class ReadingStageError(ValueError):
    def __init__(
        self,
        code: str,
        *,
        candidates: Iterable[DegradationCandidate] = (),
    ) -> None:
        descriptor = ERROR_DESCRIPTORS[code]
        super().__init__(descriptor.message)
        self.code = code
        self.category = descriptor.category
        self.safe_message = descriptor.message
        self.action = descriptor.action
        self.candidates = tuple(candidates)


def degradation_from_exception(stage: str, error: Exception) -> FlowDegradation:
    raw_code = getattr(error, "code", None)
    if isinstance(error, FileNotFoundError):
        code = "VISION_TOOL_UNAVAILABLE"
    elif isinstance(raw_code, str) and raw_code in ERROR_DESCRIPTORS:
        code = raw_code
    elif isinstance(raw_code, str) and raw_code.startswith("MODEL_"):
        code = (
            "MODEL_OUTPUT_SCHEMA_INVALID"
            if "ANALYSIS" in raw_code or "SCHEMA" in raw_code
            else "MODEL_REQUEST_FAILED"
        )
    else:
        code = "OPTIONAL_STAGE_FAILED"
    descriptor = ERROR_DESCRIPTORS[code]
    candidates = getattr(error, "candidates", ())
    return FlowDegradation(
        stage=stage,
        code=code,
        category=descriptor.category,
        message=descriptor.message,
        action=descriptor.action,
        candidates=list(candidates),
    )
