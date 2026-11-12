from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from academic_translation.agent.state import TranslationState
from academic_translation.llm.ollama import TextGenerator
from academic_translation.schemas.models import Precision, QualityReport, TermEntry, TranslationSegment
from academic_translation.tools.document_parser import parse_document
from academic_translation.tools.exporter import export_translation
from academic_translation.tools.figure_translator import translate_docx_figures
from academic_translation.tools.pdfmathtranslate_adapter import render_pdf_with_pdf2zh
from academic_translation.tools.quality import assess_quality
from academic_translation.tools.terminology_store import TerminologyStore
from academic_translation.utils.text import restore_protected_content
from academic_translation.settings import settings


NAMES = {"en": "English", "zh": "Chinese (Simplified)", "zh-cn": "Chinese (Simplified)"}


def prompt(name: str, **values: object) -> str:
    # Editable development installs keep prompts beside agent-core; the Docker
    # image installs the package but retains that source directory at /app.
    # Resolve both layouts so a container never fails after accepting an upload.
    candidates = [
        Path(os.getenv("ACADEMIC_TRANSLATION_PROMPTS_DIR", "")) if os.getenv("ACADEMIC_TRANSLATION_PROMPTS_DIR") else None,
        Path(__file__).resolve().parents[3] / "prompts",
        Path("/app/agent-core/prompts"),
    ]
    path = next((directory / name for directory in candidates if directory and (directory / name).is_file()), None)
    if path is None:
        raise FileNotFoundError(f"Academic translation prompt is missing: {name}")
    return path.read_text(encoding="utf-8").format(**values)


def parse_json_array(text: str) -> list[dict]:
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        result = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return result if isinstance(result, list) else []


def build_translation_graph(model: TextGenerator):
    def validate(state: TranslationState) -> dict:
        return {"task_id": str(uuid4()), "warnings": []}

    def parse(state: TranslationState) -> dict:
        request = state["request"]
        segments = parse_document(text=request.text, input_path=request.input_path, policy=request.element_policy)
        if request.selected_segment_ids:
            wanted = set(request.selected_segment_ids)
            segments = [item for item in segments if item.segment_id in wanted]
        if not segments:
            raise ValueError("No translatable segments were found.")
        return {"segments": segments}

    def terms(state: TranslationState) -> dict:
        request = state["request"]
        excerpt = "\n".join(item.source_text for item in state["segments"] if item.translatable)[:8000]
        store = TerminologyStore()
        library = store.load(request.domain)
        user = [TermEntry(source=k, target=v, origin="user") for k, v in request.glossary.items()]
        identified: list[TermEntry] = []
        warnings = list(state.get("warnings", []))
        try:
            for item in parse_json_array(model.generate(prompt("term_extraction.txt", source_lang=request.source_lang, target_lang=request.target_lang, glossary=json.dumps(request.glossary, ensure_ascii=False), excerpt=excerpt))):
                if item.get("source") and item.get("target"):
                    identified.append(TermEntry(source=str(item["source"]), target=str(item["target"]), confidence=float(item.get("confidence", 0.7))))
        except Exception as exc:
            warnings.append(f"Term extraction unavailable; user glossary remains active: {exc}")
        merged = {term.source.lower(): term for term in library}
        merged.update({term.source.lower(): term for term in identified})
        for term in user:
            merged[term.source.lower()] = term
        if request.persist_terms:
            store.save(request.domain, identified + user)
        return {"glossary": list(merged.values()), "warnings": warnings}

    def translate(state: TranslationState) -> dict:
        request = state["request"]
        terms = {term.source: term.target for term in state["glossary"]}
        all_terms = "\n".join(f"- {k} => {v}" for k, v in terms.items()) or "(none)"
        def translate_one(segment: TranslationSegment) -> TranslationSegment:
            if not segment.translatable:
                segment.translated_text = segment.source_text
                return segment
            applicable = {k: v for k, v in terms.items() if k.lower() in segment.source_text.lower()}
            output = model.generate(prompt("translation.txt", source_lang=request.source_lang, target_lang=request.target_lang, source_name=NAMES.get(request.source_lang.lower(), request.source_lang), target_name=NAMES.get(request.target_lang.lower(), request.target_lang), precision=request.precision.value, glossary="\n".join(f"- {k} => {v}" for k, v in applicable.items()) or all_terms, segment=segment.source_text)).strip()
            if not output:
                raise RuntimeError(f"Empty translation for {segment.segment_id}")
            for token in set(re.findall(r"\[\[KEEP_\d+\]\]", output)) - set(segment.tokens):
                output = output.replace(token, "")
            segment.translated_text = output
            return segment

        segments = list(state["segments"])
        backend = settings.translation_llm_backend.strip().lower()
        max_parallel = request.max_parallel_segments
        if backend in {"api", "openai", "openai-compatible"}:
            max_parallel = min(max_parallel, settings.translation_api_max_parallel)
        if max_parallel == 1:
            translated = [translate_one(segment) for segment in segments]
        else:
            with ThreadPoolExecutor(max_workers=max_parallel) as executor:
                translated = list(executor.map(translate_one, segments))
        return {"segments": translated}

    def polish(state: TranslationState) -> dict:
        request = state["request"]

        def polish_one(segment: TranslationSegment) -> TranslationSegment:
            if not segment.translatable:
                return segment
            output = model.generate(prompt("polish.txt", target_name=NAMES.get(request.target_lang.lower(), request.target_lang), draft=segment.translated_text)).strip()
            if not output:
                raise RuntimeError(f"Empty polished translation for {segment.segment_id}")
            for token in set(re.findall(r"\[\[KEEP_\d+\]\]", output)) - set(segment.tokens):
                output = output.replace(token, "")
            segment.translated_text = output
            return segment

        if request.max_parallel_segments == 1:
            polished = [polish_one(segment) for segment in state["segments"]]
        else:
            with ThreadPoolExecutor(max_workers=request.max_parallel_segments) as executor:
                polished = list(executor.map(polish_one, state["segments"]))
        return {"segments": polished}

    def quality(state: TranslationState) -> dict:
        return {"quality": assess_quality(state["segments"], state["glossary"], state.get("warnings", []))}

    def export(state: TranslationState) -> dict:
        request = state["request"]
        glossary = {term.source: term.target for term in state["glossary"]}
        outputs = export_translation(request, state["segments"], glossary, state["quality"].model_dump())
        if request.input_path and request.input_path.suffix.lower() == ".docx" and request.element_policy.translate_figures:
            manifest = (request.output_dir or Path("outputs")) / f"{request.input_path.stem}-docx-figure-translation-manifest.json"
            translate_docx_figures(
                Path(outputs["monolingual_docx"]),
                manifest,
                model,
                request.target_lang,
                request.element_policy.figure_min_ocr_confidence,
                glossary,
            )
            outputs["figure_translation_manifest"] = str(manifest)
        outputs.update(render_pdf_with_pdf2zh(request, glossary))
        return {"outputs": outputs}

    graph = StateGraph(TranslationState)
    for name, fn in [("validate", validate), ("parse", parse), ("terms", terms), ("translate", translate), ("polish", polish), ("quality", quality), ("export", export)]:
        graph.add_node(name, fn)
    graph.add_edge(START, "validate")
    graph.add_edge("validate", "parse")
    graph.add_edge("parse", "terms")
    graph.add_edge("terms", "translate")
    graph.add_conditional_edges("translate", lambda state: "polish" if state["request"].precision == Precision.SUBMISSION else "quality", {"polish": "polish", "quality": "quality"})
    graph.add_edge("polish", "quality")
    graph.add_edge("quality", "export")
    graph.add_edge("export", END)
    return graph.compile()
