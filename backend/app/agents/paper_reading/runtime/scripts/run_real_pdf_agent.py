#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-core" / "src"))

from llm.openai_compatible import OpenAICompatibleModelGateway  # noqa: E402
from llm.openai_compatible_vision import OpenAICompatibleVisionGateway  # noqa: E402
from paper_context.parser import PypdfTextParser  # noqa: E402
from paper_context.docling_table_extraction import DoclingTableExtractor  # noqa: E402
from paper_context.splitter import LocalSplitterGateway  # noqa: E402
from reading.page_renderer import PopplerPageRenderer  # noqa: E402
from reading.concurrency import run_concurrently, run_concurrently_flow_first  # noqa: E402
from reading.deep_report import build_unified_deep_reading_output  # noqa: E402
from reading.errors import degradation_from_exception  # noqa: E402
from reading.execution import FlowDegradation  # noqa: E402
from reading.experiments import ExperimentReproducibilityAgent  # noqa: E402
from reading.performance import (  # noqa: E402
    PipelineTimer,
    SPEED_PROFILES,
    resolve_performance_options,
)
from reading.qa import PaperScopedQaAgent  # noqa: E402
from reading.scientific_elements import FormulaFigureAnalysisAgent  # noqa: E402
from reading.service import RealPdfReadingAgent  # noqa: E402
from reading.sources import ArxivPdfDownloader  # noqa: E402


def _required(value: str | None, label: str) -> str:
    if value and value.strip() and value != "configure-at-runtime":
        return value
    raise SystemExit(f"Missing required configuration: {label}")


def _trust_environment_proxy(url: str) -> bool:
    return urlparse(url).hostname not in {"127.0.0.1", "localhost", "::1"}


def _load_model_env_files(paths: list[Path]) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in paths:
        for raw_line in path.expanduser().read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key.startswith(("CHUNK_QA_MODEL_", "PAPER_READING_")):
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            values[key] = value
    return values


def _optional_boolean(value: str | None, label: str) -> bool | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"{label} must be true or false.")


def _optional_failure_degradation(stage: str, error: Exception) -> FlowDegradation:
    return degradation_from_exception(stage, error)


def _write_utf8_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise SystemExit(f"Could not write UTF-8 output to {path}: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Read one text PDF with the Paper Reading Agent.")
    parser.add_argument("pdf", nargs="?", type=Path)
    parser.add_argument("--arxiv-id", help="Download one arXiv PDF and run the same reading flow.")
    parser.add_argument("--goal", required=True)
    parser.add_argument("--depth", choices=("OVERVIEW", "STANDARD", "DEEP"))
    parser.add_argument(
        "--speed-profile",
        choices=tuple(SPEED_PROFILES),
        help=(
            "Named defaults: fast=OVERVIEW/base only, balanced=STANDARD/base only, "
            "quality=DEEP/experiments/elements. Explicit depth and analysis flags win."
        ),
    )
    parser.add_argument(
        "--execution-mode",
        choices=("flow_first", "strict"),
        default="flow_first",
        help="Default flow_first preserves the base report when optional stages fail.",
    )
    parser.add_argument(
        "--strategy",
        choices=("fixed_boundary_v1", "paragraph_sentence_v1", "section_parent_child_v1"),
    )
    parser.add_argument("--language", default="zh-CN")
    parser.add_argument(
        "--focus-aspect",
        action="append",
        choices=(
            "RESEARCH_QUESTION", "METHOD", "EQUATION", "FIGURE", "TABLE",
            "EXPERIMENT", "INNOVATION", "LIMITATION", "REPRODUCIBILITY",
        ),
        default=[],
    )
    parser.add_argument("--question", help="Optionally ask one evidence-grounded question after reading.")
    parser.add_argument("--question-page", action="append", type=int, default=[])
    parser.add_argument("--question-section", action="append", default=[])
    parser.add_argument("--explain-text", help="Explain one selected passage or term in paper context.")
    parser.add_argument(
        "--explain-object-id",
        help="Explain one exact Equation/Figure/Table DocumentIR object ID.",
    )
    parser.add_argument(
        "--table-structure",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Use Docling accurate table structure with PyMuPDF caption anchors. "
            "It is enabled automatically when a Docling artifacts path is configured."
        ),
    )
    parser.add_argument(
        "--docling-artifacts-path",
        type=Path,
        help=(
            "Local models downloaded with 'docling-tools models download layout "
            "tableformer'; may also be set with DOCLING_ARTIFACTS_PATH."
        ),
    )
    parser.add_argument(
        "--analyze-elements",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Explain key equations, figures, and tables; enabled by default for DEEP flow_first.",
    )
    parser.add_argument(
        "--scientific-coverage",
        choices=("KEY", "COMPREHENSIVE", "SELECTED"),
        default="KEY",
        help="Analyze ranked key objects, every object, or explicit upstream object IDs.",
    )
    parser.add_argument(
        "--scientific-object-id",
        action="append",
        default=[],
        help="Upstream DocumentIR object_id to analyze in SELECTED mode; repeat as needed.",
    )
    parser.add_argument(
        "--max-scientific-elements",
        type=int,
        help="Optional cap for COMPREHENSIVE mode; by default every located object is attempted.",
    )
    parser.add_argument(
        "--analyze-experiments",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Analyze experiments and reproducibility; enabled by default for DEEP flow_first.",
    )
    parser.add_argument(
        "--vision-model",
        help="Optional vision model; without it element analysis remains text-only.",
    )
    parser.add_argument(
        "--vision-base-url",
    )
    parser.add_argument("--max-visual-pages", type=int, default=4)
    parser.add_argument(
        "--max-concurrent-model-calls",
        type=int,
        default=8,
        help="Maximum independent model calls started together after base reading.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Optionally write the unified structured report as JSON.",
    )
    parser.add_argument(
        "--timing-json-output",
        type=Path,
        help="Optionally write effective performance settings and stage timings as JSON.",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        help="Write Markdown directly as UTF-8 instead of relying on shell redirection.",
    )
    parser.add_argument(
        "--pdftoppm-path",
        type=Path,
        help="Explicit pdftoppm executable path for visual analysis.",
    )
    parser.add_argument(
        "--pdftotext-path",
        type=Path,
        help="Explicit pdftotext executable path for target-aware visual crops.",
    )
    parser.add_argument(
        "--imagemagick-path",
        type=Path,
        help="Explicit ImageMagick executable path for target-aware visual crops.",
    )
    parser.add_argument("--model-base-url")
    parser.add_argument("--model")
    parser.add_argument(
        "--model-env-file",
        action="append",
        type=Path,
        default=[],
        help="Load CHUNK_QA_MODEL_* or PAPER_READING_* settings; later files override earlier files.",
    )
    parser.add_argument(
        "--credential-env",
        help="Credential environment variable used when the model env file has no API key.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        help="Override the configured timeout for text-model and vision-model calls.",
    )
    args = parser.parse_args()

    if args.max_concurrent_model_calls < 1:
        raise SystemExit("--max-concurrent-model-calls must be positive.")
    if args.max_scientific_elements is not None and args.max_scientific_elements < 1:
        raise SystemExit("--max-scientific-elements must be positive.")
    if args.scientific_coverage == "SELECTED" and not args.scientific_object_id:
        raise SystemExit("SELECTED coverage requires --scientific-object-id.")
    if args.scientific_coverage != "SELECTED" and args.scientific_object_id:
        raise SystemExit("--scientific-object-id requires SELECTED coverage.")
    if args.explain_text and args.explain_object_id:
        raise SystemExit("Use either --explain-text or --explain-object-id, not both.")

    if (args.pdf is None) == (args.arxiv_id is None):
        raise SystemExit("Provide exactly one local PDF or --arxiv-id.")
    performance = resolve_performance_options(
        args.speed_profile,
        depth=args.depth,
        analyze_experiments=args.analyze_experiments,
        analyze_elements=args.analyze_elements,
        execution_mode=args.execution_mode,
    )
    timer = PipelineTimer()

    with timer.measure("source_acquisition"):
        temporary_source = None
        source_type = "USER_UPLOAD"
        source_uri = None
        source_path = args.pdf
        if args.arxiv_id:
            data = ArxivPdfDownloader().download(args.arxiv_id)
            temporary_source = tempfile.TemporaryDirectory(prefix="paper-reading-arxiv-")
            source_path = Path(temporary_source.name) / f"{args.arxiv_id.replace('/', '_')}.pdf"
            source_path.write_bytes(data)
            source_type = "ARXIV"
            source_uri = f"https://arxiv.org/abs/{args.arxiv_id}"
        assert source_path is not None

    with timer.measure("configuration"):
        default_model_env = ROOT / "agent-core" / ".env"
        model_env_files = [
            *([default_model_env] if default_model_env.is_file() else []),
            *args.model_env_file,
        ]
        model_file_config = _load_model_env_files(model_env_files)
        strategy = _required(
            args.strategy
            or model_file_config.get("PAPER_READING_SPLITTER_STRATEGY")
            or os.getenv("PAPER_READING_SPLITTER_STRATEGY"),
            "splitter strategy",
        )
        model_base_url = _required(
            args.model_base_url
            or model_file_config.get("CHUNK_QA_MODEL_BASE_URL")
            or model_file_config.get("PAPER_READING_MODEL_BASE_URL")
            or os.getenv("PAPER_READING_MODEL_BASE_URL"),
            "model base URL",
        )
        model_name = _required(
            args.model
            or model_file_config.get("CHUNK_QA_MODEL_NAME")
            or model_file_config.get("PAPER_READING_MODEL_NAME")
            or os.getenv("PAPER_READING_MODEL_NAME"),
            "model name",
        )
        credential_env = (
            args.credential_env
            or model_file_config.get("PAPER_READING_MODEL_CREDENTIAL_ENV")
            or os.getenv("PAPER_READING_MODEL_CREDENTIAL_ENV")
            or "OPENAI_API_KEY"
        )
        api_key = _required(
            model_file_config.get("CHUNK_QA_MODEL_API_KEY") or os.getenv(credential_env),
            credential_env,
        )
        timeout = args.timeout_seconds or float(
            model_file_config.get("CHUNK_QA_MODEL_TIMEOUT")
            or model_file_config.get("PAPER_READING_TIMEOUT_SECONDS")
            or os.getenv("PAPER_READING_TIMEOUT_SECONDS")
            or "90"
        )
        if timeout <= 0:
            raise SystemExit("--timeout-seconds must be positive.")
        text_enable_thinking = _optional_boolean(
            model_file_config.get("PAPER_READING_ENABLE_THINKING")
            or os.getenv("PAPER_READING_ENABLE_THINKING"),
            "PAPER_READING_ENABLE_THINKING",
        )
        vision_enable_thinking = _optional_boolean(
            model_file_config.get("PAPER_READING_VISION_ENABLE_THINKING")
            or os.getenv("PAPER_READING_VISION_ENABLE_THINKING"),
            "PAPER_READING_VISION_ENABLE_THINKING",
        )

        model_gateway = OpenAICompatibleModelGateway(
            model_base_url,
            api_key,
            model_name,
            timeout_seconds=timeout,
            trust_env=_trust_environment_proxy(model_base_url),
            repair_invalid_analysis=args.execution_mode == "flow_first",
            enable_thinking=text_enable_thinking,
        )
        agent = RealPdfReadingAgent(
            PypdfTextParser(),
            LocalSplitterGateway(),
            model_gateway,
            max_claim_verification_workers=min(
                4,
                args.max_concurrent_model_calls,
            ),
        )
    with timer.measure("base_reading"):
        output = agent.read_pdf(
            source_path,
            reading_goal=args.goal,
            depth=performance.depth,
            splitter_strategy=strategy,
            language=args.language,
            focus_aspects=args.focus_aspect or None,
            source_type=source_type,
            source_uri=source_uri,
            arxiv_id=args.arxiv_id,
            execution_mode=args.execution_mode,
        )
    analyze_experiments = performance.analyze_experiments
    analyze_elements = performance.analyze_elements
    optional_tasks = {}
    vision_gateway = None
    if analyze_experiments:
        optional_tasks["experiments"] = lambda: ExperimentReproducibilityAgent(
            model_gateway
        ).analyze(output)
    if analyze_elements:
        page_renderer = None
        vision_model = (
            args.vision_model
            or model_file_config.get("PAPER_READING_VISION_MODEL_NAME")
            or os.getenv("PAPER_READING_VISION_MODEL_NAME")
        )
        if vision_model:
            vision_base_url = (
                args.vision_base_url
                or model_file_config.get("PAPER_READING_VISION_MODEL_BASE_URL")
                or os.getenv("PAPER_READING_VISION_MODEL_BASE_URL")
                or model_base_url
            )
            vision_gateway = OpenAICompatibleVisionGateway(
                vision_base_url,
                api_key,
                vision_model,
                timeout_seconds=timeout,
                trust_env=_trust_environment_proxy(vision_base_url),
                enable_thinking=vision_enable_thinking,
            )
            page_renderer = PopplerPageRenderer(
                executable=args.pdftoppm_path or "pdftoppm",
                pdftotext_executable=args.pdftotext_path,
                image_magick_executable=args.imagemagick_path,
            )
        docling_artifacts_path = args.docling_artifacts_path or os.getenv(
            "DOCLING_ARTIFACTS_PATH"
        )
        use_table_structure = (
            args.table_structure
            if args.table_structure is not None
            else docling_artifacts_path is not None
        )
        if use_table_structure and docling_artifacts_path is None:
            raise SystemExit(
                "Table structure requires --docling-artifacts-path or "
                "DOCLING_ARTIFACTS_PATH."
            )
        table_extractor = (
            DoclingTableExtractor(artifacts_path=docling_artifacts_path)
            if use_table_structure
            else None
        )
        optional_tasks["scientific_elements"] = lambda: FormulaFigureAnalysisAgent(
            model_gateway,
            vision_gateway=vision_gateway,
            page_renderer=page_renderer,
            table_extractor=table_extractor,
            max_visual_pages=args.max_visual_pages,
            max_concurrent_visual_calls=args.max_concurrent_model_calls,
            max_concurrent_text_calls=args.max_concurrent_model_calls,
        ).analyze(
            output,
            source_path,
            coverage_mode=args.scientific_coverage,
            max_scientific_elements=args.max_scientific_elements,
            selected_object_ids=args.scientific_object_id or None,
        )
    if args.question:
        optional_tasks["question"] = lambda: PaperScopedQaAgent(model_gateway).ask(
            output,
            args.question,
            page_numbers=set(args.question_page) or None,
            section_path=args.question_section or None,
        )
    if args.explain_text:
        optional_tasks["explanation"] = lambda: PaperScopedQaAgent(
            model_gateway
        ).explain_selection(
            output,
            args.explain_text,
            page_number=args.question_page[0] if args.question_page else None,
        )
    if args.explain_object_id:
        optional_tasks["explanation"] = lambda: PaperScopedQaAgent(
            model_gateway
        ).explain_object(
            output,
            args.explain_object_id,
        )
    optional_failures: dict[str, Exception] = {}
    with timer.measure("optional_analysis"):
        if args.execution_mode == "flow_first":
            optional_outputs, optional_failures = run_concurrently_flow_first(
                optional_tasks,
                max_workers=args.max_concurrent_model_calls,
            )
        else:
            optional_outputs = run_concurrently(
                optional_tasks,
                max_workers=args.max_concurrent_model_calls,
            )
    experiment_output = optional_outputs.get("experiments")
    element_output = optional_outputs.get("scientific_elements")
    qa_output = optional_outputs.get("question")
    explanation_output = optional_outputs.get("explanation")
    requested_stages = {
        "experiments": analyze_experiments,
        "scientific_elements": analyze_elements,
        "question": bool(args.question),
        "explanation": bool(args.explain_text or args.explain_object_id),
    }
    stage_statuses = {
        name: (
            "FAILED_CONTINUED"
            if name in optional_failures
            else "COMPLETED"
            if requested
            else "NOT_REQUESTED"
        )
        for name, requested in requested_stages.items()
    }
    with timer.measure("output"):
        unified = build_unified_deep_reading_output(
            output,
            scientific=element_output,
            experiments=experiment_output,
            qa=qa_output,
            explanation=explanation_output,
            execution_mode=args.execution_mode,
            stage_statuses=stage_statuses,
            degradations=[
                _optional_failure_degradation(name, error)
                for name, error in optional_failures.items()
            ],
        )
        if args.markdown_output:
            _write_utf8_atomic(args.markdown_output, unified.markdown)
        else:
            sys.stdout.write(unified.markdown)
        if args.json_output:
            _write_utf8_atomic(args.json_output, unified.json_text + "\n")
    timing = timer.snapshot()
    timing_payload = {
        "schema_version": "1.1",
        "speed_profile": performance.profile_name,
        "effective_configuration": {
            "depth": performance.depth,
            "execution_mode": args.execution_mode,
            "analyze_experiments": analyze_experiments,
            "analyze_elements": analyze_elements,
            "scientific_coverage": args.scientific_coverage,
            "max_concurrent_model_calls": args.max_concurrent_model_calls,
            "max_claim_verification_workers": min(
                4,
                args.max_concurrent_model_calls,
            ),
        },
        "model_requests": {
            "text": model_gateway.request_metrics_snapshot(),
            **(
                {"vision": vision_gateway.request_metrics_snapshot()}
                if vision_gateway is not None
                else {}
            ),
        },
        **timing,
    }
    stage_text = " ".join(
        f"{name}={seconds:.3f}s"
        for name, seconds in timing["stages_seconds"].items()
    )
    print(
        f"[timing] profile={performance.profile_name} depth={performance.depth} "
        f"{stage_text} total={timing['total_seconds']:.3f}s "
        f"text_model_requests="
        f"{timing_payload['model_requests']['text']['request_count']}",
        file=sys.stderr,
    )
    if args.timing_json_output:
        _write_utf8_atomic(
            args.timing_json_output,
            json.dumps(timing_payload, ensure_ascii=False, indent=2) + "\n",
        )
    model_gateway.close()
    if vision_gateway is not None:
        vision_gateway.close()
    if temporary_source is not None:
        temporary_source.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
