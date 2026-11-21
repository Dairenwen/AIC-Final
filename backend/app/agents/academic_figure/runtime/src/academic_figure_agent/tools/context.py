from __future__ import annotations

from pathlib import Path

import fitz
from docx import Document
from langchain_core.tools import BaseTool
from PIL import Image
from pydantic import BaseModel, Field

from config.constants import SUPPORTED_CONTEXT_SUFFIXES, SUPPORTED_IMAGE_SUFFIXES
from config.settings import Settings, get_settings


class ContextExtractionInput(BaseModel):
    context_files: list[str] = Field(default_factory=list)
    sketch_files: list[str] = Field(default_factory=list)


class ContextExtractionTool(BaseTool):
    name: str = "context_extraction"
    description: str = "Extract paper text and inspect sketch/image metadata for figure planning."
    args_schema: type[BaseModel] = ContextExtractionInput

    def _run(self, context_files: list[str], sketch_files: list[str]) -> str:
        return extract_context([Path(item) for item in context_files], [Path(item) for item in sketch_files])


def extract_context(
    context_files: list[Path],
    sketch_files: list[Path],
    settings: Settings | None = None,
) -> str:
    resolved = settings or get_settings()
    sections: list[str] = []
    for raw_path in context_files:
        path = raw_path.expanduser().resolve()
        _validate(path, resolved.max_input_file_mb)
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_CONTEXT_SUFFIXES:
            raise ValueError(f"Unsupported context file: {path.name}")
        text = _read_context_file(path)
        sections.append(f"[Context: {path.name}]\n{text[:20000]}")

    for raw_path in sketch_files:
        path = raw_path.expanduser().resolve()
        _validate(path, resolved.max_input_file_mb)
        if path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            raise ValueError(f"Unsupported sketch file: {path.name}")
        with Image.open(path) as image:
            sections.append(
                f"[Sketch: {path.name}] size={image.width}x{image.height}, "
                f"mode={image.mode}, format={image.format}"
            )
    return "\n\n".join(sections)


def _read_context_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        with fitz.open(path) as document:
            return "\n".join(page.get_text("text") for page in document)
    if suffix == ".docx":
        document = Document(path)
        return "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
    return path.read_text(encoding="utf-8", errors="replace")


def _validate(path: Path, max_mb: int) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Input file does not exist: {path}")
    if path.stat().st_size > max_mb * 1024 * 1024:
        raise ValueError(f"Input file exceeds {max_mb} MB limit: {path.name}")
