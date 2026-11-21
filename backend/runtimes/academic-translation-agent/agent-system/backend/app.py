from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

# The source checkout and the container have different directory depth.  Keep
# the checkout default, while Compose pins the runtime root to /app.
ROOT = Path(os.getenv("ACADEMIC_TRANSLATION_ROOT", Path(__file__).resolve().parents[2]))
OUTPUTS_ROOT = ROOT / "agent-core" / "outputs"
sys.path.insert(0, str(ROOT / "agent-core" / "src"))

from academic_translation.agent.service import AcademicTranslationAgent
from academic_translation.settings import settings
from academic_translation.schemas.models import Precision, TranslationRequest

app = FastAPI(title="Academic Translation Agent", version="0.1.0")
agent = AcademicTranslationAgent()
tasks: dict[str, tuple[TranslationRequest, object]] = {}


class SegmentEdit(BaseModel):
    translated_text: str


def remember(request: TranslationRequest):
    result = agent.translate(request)
    tasks[result.task_id] = (request, result)
    return result


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "provider": "local-ollama-only", "model": settings.ollama_translation_model}


@app.post("/translate/text")
def translate_text(text: str, source_lang: str = "en", target_lang: str = "zh", precision: Precision = Precision.READING, domain: str = "academic", persist_terms: bool = False, max_parallel_segments: int = 2) -> dict:
    request = TranslationRequest(text=text, source_lang=source_lang, target_lang=target_lang, precision=precision, domain=domain, persist_terms=persist_terms, max_parallel_segments=max_parallel_segments, output_dir=OUTPUTS_ROOT)
    return remember(request).model_dump(mode="json")


@app.post("/translate/document")
def translate_document(file: UploadFile = File(...), source_lang: str = Form("en"), target_lang: str = Form("zh"), precision: Precision = Form(Precision.READING), preserve_pdf_layout: bool = Form(False), pdf_only: bool = Form(False), pdf_bilingual: bool = Form(False), pdf_layout_mode: str = Form("batch"), release_model_between_pages: bool = Form(False), low_memory_chunk_pages: int = Form(2), ignore_pdf_cache: bool = Form(False), preserve_formulas: bool = Form(True), preserve_figures: bool = Form(True), translate_figures: bool = Form(False), preserve_references: bool = Form(True), preserve_headers_footers: bool = Form(True), glossary_json: str = Form("{}"), domain: str = Form("academic"), persist_terms: bool = Form(False), max_parallel_segments: int = Form(2), include_bilingual_markdown: bool = Form(False), max_output_bytes: int = Form(10_000_000), pdf_timeout_seconds: int = Form(600)) -> dict:
    suffix = Path(file.filename or "input.txt").suffix.lower()
    if suffix not in {".txt", ".md", ".docx", ".pdf"}:
        raise HTTPException(status_code=400, detail="Supported files: .txt, .md, .docx, .pdf")
    uploads = OUTPUTS_ROOT / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    destination = uploads / (file.filename or f"input{suffix}")
    with destination.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    try:
        glossary = json.loads(glossary_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="glossary_json must be a JSON object") from exc
    if not isinstance(glossary, dict) or not all(isinstance(source, str) and isinstance(target, str) for source, target in glossary.items()):
        raise HTTPException(status_code=422, detail="glossary_json must map string source terms to string target terms")
    from academic_translation.schemas.models import ElementPolicy
    request = TranslationRequest(input_path=destination, source_lang=source_lang, target_lang=target_lang, precision=precision, glossary=glossary, output_dir=OUTPUTS_ROOT, preserve_pdf_layout=preserve_pdf_layout, pdf_only=pdf_only, pdf_bilingual=pdf_bilingual, pdf_layout_mode=pdf_layout_mode, release_model_between_pages=release_model_between_pages, low_memory_chunk_pages=low_memory_chunk_pages, ignore_pdf_cache=ignore_pdf_cache, element_policy=ElementPolicy(preserve_formulas=preserve_formulas, preserve_figures=preserve_figures, translate_figures=translate_figures, preserve_references=preserve_references, preserve_headers_footers=preserve_headers_footers), domain=domain, persist_terms=persist_terms, max_parallel_segments=max_parallel_segments, include_bilingual_markdown=include_bilingual_markdown, max_output_bytes=max_output_bytes, pdf_timeout_seconds=pdf_timeout_seconds)
    return remember(request).model_dump(mode="json")


@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    item = tasks.get(task_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Task not found; tasks are kept only while this API process runs.")
    return item[1].model_dump(mode="json")


@app.patch("/tasks/{task_id}/segments/{segment_id}")
def edit_segment(task_id: str, segment_id: str, edit: SegmentEdit) -> dict:
    item = tasks.get(task_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Task not found")
    request, result = item
    try:
        revised = agent.revise_segment(request, result, segment_id, edit.translated_text)
    except KeyError:
        raise HTTPException(status_code=404, detail="Segment not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    tasks[task_id] = (request, revised)
    return revised.model_dump(mode="json")
