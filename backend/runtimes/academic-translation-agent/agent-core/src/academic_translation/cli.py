from __future__ import annotations

import argparse
import json
from pathlib import Path

from academic_translation.agent.service import AcademicTranslationAgent
from academic_translation.schemas.models import ElementPolicy, Precision, TranslationRequest


def main() -> None:
    parser = argparse.ArgumentParser(description="Single-model local academic translation Agent")
    parser.add_argument("input", help="Literal selected text with --text, or a .txt/.md/.docx/.pdf path")
    parser.add_argument("--text", action="store_true", help="Interpret input as a selected text passage")
    parser.add_argument("--from", dest="source_lang", default="en")
    parser.add_argument("--to", dest="target_lang", default="zh")
    parser.add_argument("--precision", choices=[item.value for item in Precision], default="reading")
    parser.add_argument("--glossary", action="append", default=[], metavar="SOURCE=TARGET")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--parallel", type=int, default=1, help="Concurrent translation requests; API mode defaults to one request for provider stability.")
    parser.add_argument("--preserve-pdf-layout", action="store_true")
    parser.add_argument("--pdf-only", action="store_true", help="Fast PDF-only export: avoid duplicate segment-by-segment translation before layout rendering")
    parser.add_argument("--pdf-bilingual", action="store_true", help="Also export original-plus-translation PDF without leaving the fast PDF-only path")
    parser.add_argument("--bilingual-markdown", action="store_true", help="Write the much larger source-and-translation Markdown review file")
    parser.add_argument("--pdf-layout-mode", choices=["batch", "pagewise", "low_memory"], default="batch", help="PDFMathTranslate execution mode; batch is fastest, low_memory is the stable per-page fallback")
    parser.add_argument("--release-model-between-pages", action="store_true", help="Only for --pdf-layout-mode low_memory: unload the local model after each page to cap memory")
    parser.add_argument("--low-memory-chunk-pages", type=int, default=2, choices=range(1, 5), metavar="1-4", help="Pages per low-memory PDFMathTranslate run; 2 is the balanced default, 1 gives the lowest memory ceiling")
    parser.add_argument("--ignore-pdf-cache", action="store_true", help="Benchmark only: force PDFMathTranslate to perform fresh model inference")
    parser.add_argument("--pdf-timeout-seconds", type=int, default=600, help="Fail a stalled PDF layout task predictably instead of waiting indefinitely")
    parser.add_argument("--domain", default="academic", help="Local terminology library namespace")
    parser.add_argument("--save-terms", action="store_true", help="Persist newly identified terms to the local library")
    parser.add_argument("--preserve-formulas", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--preserve-figures", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--translate-figures", action="store_true", help="Translate safe PDF visual elements: raster figure labels and recognised table headers (Chinese target only)")
    parser.add_argument("--preserve-references", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--preserve-headers-footers", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    glossary: dict[str, str] = {}
    for item in args.glossary:
        if "=" not in item:
            parser.error("--glossary must be SOURCE=TARGET")
        key, value = item.split("=", 1)
        glossary[key.strip()] = value.strip()
    request = TranslationRequest(text=args.input if args.text else None, input_path=None if args.text else Path(args.input), source_lang=args.source_lang, target_lang=args.target_lang, precision=Precision(args.precision), glossary=glossary, domain=args.domain, persist_terms=args.save_terms, element_policy=ElementPolicy(preserve_formulas=args.preserve_formulas, preserve_figures=args.preserve_figures, preserve_references=args.preserve_references, preserve_headers_footers=args.preserve_headers_footers, translate_figures=args.translate_figures), output_dir=args.output_dir, max_parallel_segments=args.parallel, preserve_pdf_layout=args.preserve_pdf_layout, pdf_only=args.pdf_only, pdf_bilingual=args.pdf_bilingual, pdf_layout_mode=args.pdf_layout_mode, release_model_between_pages=args.release_model_between_pages, low_memory_chunk_pages=args.low_memory_chunk_pages, ignore_pdf_cache=args.ignore_pdf_cache, include_bilingual_markdown=args.bilingual_markdown, pdf_timeout_seconds=args.pdf_timeout_seconds)
    print(json.dumps(AcademicTranslationAgent().translate(request).model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
