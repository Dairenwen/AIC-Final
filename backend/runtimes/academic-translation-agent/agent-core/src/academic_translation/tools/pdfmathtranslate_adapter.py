from __future__ import annotations

import subprocess
import shutil
import tempfile
import time
import os
from collections.abc import Mapping
from pathlib import Path

import fitz

from academic_translation.schemas.models import TranslationRequest
from academic_translation.llm.factory import build_academic_llm
from academic_translation.settings import settings
from academic_translation.tools.figure_translator import translate_pdf_figures
from academic_translation.tools.table_translator import translate_pdf_tables


def _wait_for(path: Path, timeout: int = 90) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return True
        time.sleep(1)
    return False


def _pdf2zh_prompt_path(glossary: Mapping[str, str] | None) -> str | None:
    """Create the file-based template required by the pinned PDFMathTranslate CLI."""
    if not glossary:
        return None
    terms = "\n".join(f"- {source} => {target}" for source, target in (glossary or {}).items())
    handle = tempfile.NamedTemporaryFile(mode="w", prefix="academic-agent-pdf2zh-", suffix=".txt", encoding="utf-8", delete=False)
    with handle:
        handle.write(
            "You are a professional academic translation engine. Return only the translation.\n"
            "Translate from $lang_in to $lang_out. Preserve equations, citations, numbers, "
            "DOIs, URLs, chemical names, formulae, gene/protein symbols, units, variables, "
            "method identifiers, table values, and all-caps abbreviations exactly.\n"
            f"Required terminology:\n{terms}\n\nSource Text: $text\n\nTranslated Text:"
        )
    return handle.name


def _run_pdf2zh(request: TranslationRequest, output: Path, glossary: Mapping[str, str] | None, pages: str | None = None) -> subprocess.CompletedProcess:
    backend = settings.translation_llm_backend.strip().lower()
    if backend in {"api", "openai", "openai-compatible"}:
        if not settings.translation_api_model or not settings.translation_api_base_url:
            raise ValueError("TRANSLATION_API_BASE_URL and TRANSLATION_API_MODEL are required for PDF translation.")
        service = f"openai:{settings.translation_api_model}"
    elif backend == "ollama":
        service = f"ollama:{settings.ollama_translation_model}"
    else:
        raise ValueError("Unsupported TRANSLATION_LLM_BACKEND. Use api or ollama.")
    command = [
        settings.pdf2zh_command,
        str(request.input_path),
        "--lang-in", request.source_lang,
        "--lang-out", request.target_lang,
        "--service", service,
        "--thread", str(request.max_parallel_segments),
        "--output", str(output),
    ]
    if prompt_path := _pdf2zh_prompt_path(glossary):
        command.extend(["--prompt", prompt_path])
    if pages is not None:
        command.extend(["--pages", pages])
    # Font subsetting is deliberately left enabled. Complete CJK font programs
    # can add tens of megabytes to a compact academic paper, while the subset
    # retains only the glyphs the translated PDF actually uses.
    if request.ignore_pdf_cache:
        command.append("--ignore-cache")
    try:
        env = os.environ.copy()
        if backend in {"api", "openai", "openai-compatible"}:
            env.update(
                {
                    "OPENAI_BASE_URL": settings.translation_api_base_url.rstrip("/"),
                    "OPENAI_API_KEY": settings.translation_api_key,
                    "OPENAI_MODEL": settings.translation_api_model,
                }
            )
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=request.pdf_timeout_seconds,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            command,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + f"PDF layout translation exceeded {request.pdf_timeout_seconds} seconds.",
        )
    finally:
        if prompt_path:
            Path(prompt_path).unlink(missing_ok=True)


def _valid_pdf(path: Path, expected_pages: int) -> bool:
    if not _wait_for(path):
        return False
    document = fitz.open(path)
    try:
        return len(document) == expected_pages
    finally:
        document.close()


def _save_compact_pdf(document: fitz.Document, output: Path) -> None:
    """Save without quality loss while reclaiming duplicate PDF resources.

    Low-memory chunks contain identical embedded CJK font programs.  The
    strongest PyMuPDF reclamation mode de-duplicates these byte-identical
    objects after merging; it never rasterises, down-samples, or reflows pages.
    """
    document.save(output, garbage=4, deflate=True, use_objstms=1)


def _enforce_output_limit(outputs: dict[str, str], limit: int) -> dict[str, str]:
    """Keep oversized artifacts internal instead of returning a broken download."""
    for artifact in ("pdf_monolingual", "pdf_bilingual"):
        value = outputs.get(artifact)
        if value and Path(value).exists() and Path(value).stat().st_size > limit:
            size = Path(value).stat().st_size
            outputs.pop("pdf_monolingual", None)
            outputs.pop("pdf_bilingual", None)
            outputs["pdf_layout_warning"] = (
                f"Translated PDF is {size:,} bytes, above the {limit:,}-byte delivery limit; "
                "no oversized PDF was returned."
            )
            return outputs
    return outputs


def render_pdf_with_pdf2zh(request: TranslationRequest, glossary: Mapping[str, str] | None = None) -> dict[str, str]:
    """Render a complete PDF with one batch invocation or a pagewise fallback.

    Batch mode sends the document once to PDFMathTranslate, which already chunks
    text and invokes the configured local model.  It replaces the former N-page
    process startup loop and is materially faster without changing model, prompt
    constraints, or target language.
    """
    if not (request.input_path and request.input_path.suffix.lower() == ".pdf" and request.preserve_pdf_layout):
        return {}
    if not settings.pdf2zh_command:
        return {"pdf_layout_warning": "PDF2ZH_COMMAND is not configured."}
    output_dir = request.output_dir or Path("outputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    base = request.input_path.stem
    source = fitz.open(request.input_path)
    source_pages = len(source)
    mono, dual = output_dir / f"{base}-mono.pdf", output_dir / f"{base}-dual.pdf"
    # A dual PDF duplicates every page.  It is now an explicit review export
    # instead of an implicit side effect of a layout-preserved translation.
    need_dual = request.pdf_bilingual
    # Only the fast PDF-only bilingual request reuses the upstream dual output.
    # The complete path deliberately keeps its established local assembly.
    # A low-memory run translates isolated one-page source PDFs.  Its upstream
    # dual PDFs therefore describe those temporary files, so build the final
    # document-level bilingual PDF locally instead of copying a page fragment.
    use_upstream_dual = request.pdf_only and request.pdf_bilingual and request.pdf_layout_mode == "batch"
    started = time.monotonic()
    merged_mono, merged_dual = fitz.open(), fitz.open()
    try:
        if request.pdf_layout_mode == "batch":
            batch_dir = output_dir / f"{base}-pdf2zh-batch"
            batch_dir.mkdir(parents=True, exist_ok=True)
            finished = _run_pdf2zh(request, batch_dir, glossary)
            batch_mono = batch_dir / f"{base}-mono.pdf"
            if finished.returncode or not _valid_pdf(batch_mono, source_pages):
                detail = finished.stderr[-500:] if finished.stderr else "batch output was missing or had an unexpected page count"
                return {"pdf_layout_warning": f"Batch PDF translation failed: {detail}"}
            shutil.copyfile(batch_mono, mono)
            if use_upstream_dual:
                batch_dual = batch_dir / f"{base}-dual.pdf"
                if not _valid_pdf(batch_dual, source_pages * 2):
                    return {"pdf_layout_warning": "Batch bilingual PDF page count validation failed."}
                shutil.copyfile(batch_dual, dual)
            # Batch output normally has no duplicated chunk fonts, but the
            # same lossless compaction also removes redundant PDF objects.
            batch_document = fitz.open(mono)
            compact_mono = output_dir / f"{base}-mono-compact.pdf"
            try:
                _save_compact_pdf(batch_document, compact_mono)
            finally:
                batch_document.close()
            compact_mono.replace(mono)
            if use_upstream_dual:
                batch_document = fitz.open(dual)
                compact_dual = output_dir / f"{base}-dual-compact.pdf"
                try:
                    _save_compact_pdf(batch_document, compact_dual)
                finally:
                    batch_document.close()
                compact_dual.replace(dual)
        else:
            if request.pdf_layout_mode == "low_memory":
                work_dir = output_dir / f"{base}-pdf2zh-low-memory"
                work_dir.mkdir(parents=True, exist_ok=True)
                chunk_size = request.low_memory_chunk_pages
                for first_page in range(0, source_pages, chunk_size):
                    last_page = min(source_pages, first_page + chunk_size) - 1
                    chunk_label = f"pages-{first_page + 1:04d}-{last_page + 1:04d}"
                    page_source = work_dir / f"{chunk_label}-source.pdf"
                    chunk_source = fitz.open()
                    try:
                        chunk_source.insert_pdf(source, from_page=first_page, to_page=last_page)
                        _save_compact_pdf(chunk_source, page_source)
                    finally:
                        chunk_source.close()
                    expected_chunk_pages = last_page - first_page + 1
                    page_dir = work_dir / chunk_label
                    page_dir.mkdir(parents=True, exist_ok=True)
                    page_request = request.model_copy(update={"input_path": page_source, "pdf_layout_mode": "batch"})
                    finished = _run_pdf2zh(page_request, page_dir, glossary)
                    page_mono = page_dir / f"{page_source.stem}-mono.pdf"
                    if finished.returncode or not _valid_pdf(page_mono, expected_chunk_pages):
                        detail = finished.stderr[-500:] if finished.stderr else "no translated page was produced"
                        return {"pdf_layout_warning": f"Low-memory pages {first_page + 1}-{last_page + 1}/{source_pages} failed: {detail}"}
                    translated = fitz.open(page_mono)
                    try:
                        merged_mono.insert_pdf(translated)
                    finally:
                        translated.close()
                    if request.release_model_between_pages and settings.translation_llm_backend.strip().lower() == "ollama":
                        subprocess.run(["ollama", "stop", settings.ollama_translation_model], text=True, capture_output=True, check=False, timeout=60)
                _save_compact_pdf(merged_mono, mono)
            else:
                work_dir = output_dir / f"{base}-pdf2zh-pages"
                work_dir.mkdir(parents=True, exist_ok=True)
                for page_index in range(source_pages):
                    page_dir = work_dir / f"page-{page_index + 1:04d}"
                    page_dir.mkdir(parents=True, exist_ok=True)
                    finished = _run_pdf2zh(request, page_dir, glossary, str(page_index + 1))
                    page_mono = page_dir / f"{base}-mono.pdf"
                    if finished.returncode or not _wait_for(page_mono):
                        detail = finished.stderr[-500:] if finished.stderr else "no translated page was produced"
                        return {"pdf_layout_warning": f"Page {page_index + 1}/{source_pages} failed: {detail}"}
                    translated = fitz.open(page_mono)
                    try:
                        translated_page = page_index if len(translated) > page_index else 0
                        merged_mono.insert_pdf(translated, from_page=translated_page, to_page=translated_page)
                    finally:
                        translated.close()
                _save_compact_pdf(merged_mono, mono)
        if not _valid_pdf(mono, source_pages):
            return {"pdf_layout_warning": "Monolingual PDF page count validation failed."}
        if need_dual and not use_upstream_dual:
            translated = fitz.open(mono)
            try:
                for page_index in range(source_pages):
                    merged_dual.insert_pdf(source, from_page=page_index, to_page=page_index)
                    merged_dual.insert_pdf(translated, from_page=page_index, to_page=page_index)
                _save_compact_pdf(merged_dual, dual)
            finally:
                translated.close()
    finally:
        source.close()
        merged_mono.close()
        merged_dual.close()
    outputs = {"pdf_monolingual": str(mono), "pdf_layout_pages": str(source_pages), "pdf_layout_mode": request.pdf_layout_mode, "pdf_layout_elapsed_seconds": f"{time.monotonic() - started:.2f}"}
    if need_dual:
        if not _valid_pdf(dual, source_pages * 2):
            return {"pdf_layout_warning": "Bilingual PDF page count validation failed."}
        outputs["pdf_bilingual"] = str(dual)
    if not request.element_policy.translate_figures:
        return _enforce_output_limit(outputs, request.max_output_bytes)
    if not request.target_lang.lower().startswith("zh"):
        outputs["visual_translation_warning"] = "Safe image and table overlays currently support Chinese targets only."
        return _enforce_output_limit(outputs, request.max_output_bytes)
    figure_mono = output_dir / f"{base}-mono-figures.pdf"
    figure_manifest = output_dir / f"{base}-figure-translation-manifest.json"
    figures_changed = translate_pdf_figures(
        mono,
        figure_mono,
        figure_manifest,
        build_academic_llm(),
        request.target_lang,
        request.element_policy.figure_min_ocr_confidence,
        request.glossary,
    )
    outputs["figure_translation_manifest"] = str(figure_manifest)
    table_mono = output_dir / f"{base}-mono-visuals.pdf"
    table_manifest = output_dir / f"{base}-table-translation-manifest.json"
    tables_changed = translate_pdf_tables(
        figure_mono if figures_changed else mono,
        table_mono,
        table_manifest,
        request.target_lang,
        request.glossary,
    )
    outputs["table_translation_manifest"] = str(table_manifest)
    if not (figures_changed or tables_changed):
        outputs["visual_translation_warning"] = "No image labels or table cells met the safe overlay criteria; original visuals were preserved."
        return _enforce_output_limit(outputs, request.max_output_bytes)
    visual_mono = table_mono if tables_changed else figure_mono
    outputs["pdf_monolingual"] = str(visual_mono)
    if need_dual:
        visual_dual = output_dir / f"{base}-dual-visuals.pdf"
        original, translated, bilingual = fitz.open(request.input_path), fitz.open(visual_mono), fitz.open()
        try:
            for page_index in range(source_pages):
                bilingual.insert_pdf(original, from_page=page_index, to_page=page_index)
                bilingual.insert_pdf(translated, from_page=page_index, to_page=page_index)
            _save_compact_pdf(bilingual, visual_dual)
        finally:
            original.close()
            translated.close()
            bilingual.close()
        outputs["pdf_bilingual"] = str(visual_dual)
    return _enforce_output_limit(outputs, request.max_output_bytes)
