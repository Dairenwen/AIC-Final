from academic_translation.agent.graph import build_translation_graph
from academic_translation.llm.factory import build_academic_llm
from academic_translation.llm.ollama import TextGenerator
from uuid import uuid4

from academic_translation.schemas.models import QualityReport, TermEntry, TranslationRequest, TranslationResult
from academic_translation.settings import settings
from academic_translation.tools.exporter import export_translation
from academic_translation.tools.quality import assess_quality
from academic_translation.tools.pdfmathtranslate_adapter import render_pdf_with_pdf2zh
from academic_translation.tools.terminology_store import TerminologyStore


class AcademicTranslationAgent:
    def __init__(self, model: TextGenerator | None = None) -> None:
        self.model = model or build_academic_llm()
        self.graph = build_translation_graph(self.model)

    def translate(self, request: TranslationRequest) -> TranslationResult:
        if request.pdf_only:
            return self._translate_pdf_only(request)
        state = self.graph.invoke({"request": request})
        return TranslationResult(task_id=state["task_id"], source_lang=request.source_lang, target_lang=request.target_lang, precision=request.precision, segments=state["segments"], glossary=state["glossary"], quality=state["quality"], outputs=state["outputs"], warnings=state.get("warnings", []))

    def _translate_pdf_only(self, request: TranslationRequest) -> TranslationResult:
        """Render a layout-preserved PDF without translating it a second time.

        The full graph is intentionally not invoked here: PDFMathTranslate is
        already responsible for translating the document with the same local
        Ollama model.  Running all extracted paragraphs through LangGraph first
        roughly doubles GPU work while its Markdown/DOCX products are unused.
        """
        if not (request.input_path and request.input_path.suffix.lower() == ".pdf" and request.preserve_pdf_layout):
            raise ValueError("--pdf-only requires a PDF input together with --preserve-pdf-layout.")
        store = TerminologyStore()
        merged: dict[str, TermEntry] = {term.source.casefold(): term for term in store.load(request.domain)}
        for source, target in request.glossary.items():
            merged[source.casefold()] = TermEntry(source=source, target=target, origin="user")
        glossary = list(merged.values())
        outputs = render_pdf_with_pdf2zh(request, {term.source: term.target for term in glossary})
        warnings = [
            "Fast PDF-only mode uses the local PDF layout engine directly; structured Markdown/DOCX exports and segment-level quality reports are intentionally skipped to avoid duplicate model inference."
        ]
        if warning := outputs.get("pdf_layout_warning"):
            warnings.append(warning)
        return TranslationResult(
            task_id=str(uuid4()),
            source_lang=request.source_lang,
            target_lang=request.target_lang,
            precision=request.precision,
            segments=[],
            glossary=glossary,
            quality=QualityReport(total_segments=0, translated_segments=0, warnings=warnings.copy()),
            outputs=outputs,
            warnings=warnings,
        )

    def revise_segment(self, request: TranslationRequest, result: TranslationResult, segment_id: str, translated_text: str) -> TranslationResult:
        segment = next((item for item in result.segments if item.segment_id == segment_id), None)
        if segment is None:
            raise KeyError(segment_id)
        if not segment.translatable:
            raise ValueError("This segment is configured as non-translatable.")
        # API editors work with the visible formula/citation text, while the workflow stores
        # stable tokens internally for validation and export.
        for token, visible in segment.tokens.items():
            translated_text = translated_text.replace(visible, token)
        segment.translated_text = translated_text
        result.quality = assess_quality(result.segments, result.glossary, result.warnings)
        glossary = {term.source: term.target for term in result.glossary}
        result.outputs = export_translation(request, result.segments, glossary, result.quality.model_dump())
        return result
