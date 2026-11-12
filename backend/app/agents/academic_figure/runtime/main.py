from __future__ import annotations

import argparse
from pathlib import Path

from academic_figure_agent import FigureRequest, run_figure_agent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Academic Figure Agent powered by Alibaba Bailian")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate an academic figure artifact bundle")
    generate.add_argument("prompt", help="Natural-language figure requirement")
    generate.add_argument("--data", action="append", default=[], help="CSV/TSV/Excel/JSON file; repeatable")
    generate.add_argument(
        "--context",
        action="append",
        default=[],
        help="PDF/DOCX/TXT/MD/TEX file; repeatable",
    )
    generate.add_argument("--sketch", action="append", default=[], help="Sketch or image file; repeatable")
    generate.add_argument(
        "--type",
        default="auto",
        choices=["auto", "line", "bar", "scatter", "box", "heatmap", "flowchart", "image_panel"],
    )
    generate.add_argument("--output", type=Path)
    generate.add_argument("--formats", nargs="+", default=["pdf", "svg", "png"])
    generate.add_argument("--code", nargs="+", default=["python", "r", "latex", "mermaid"])
    generate.add_argument("--languages", nargs="+", default=["zh", "en"])
    generate.add_argument("--offline", action="store_true", help="Use deterministic planning without Bailian")

    request_file = subparsers.add_parser("request", help="Run from a FigureRequest JSON file")
    request_file.add_argument("file", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "request":
        request = FigureRequest.model_validate_json(args.file.read_text(encoding="utf-8"))
    else:
        request = FigureRequest(
            prompt=args.prompt,
            data_files=[Path(path) for path in args.data],
            context_files=[Path(path) for path in args.context],
            sketch_files=[Path(path) for path in args.sketch],
            output_dir=args.output,
            figure_type=args.type,
            export_formats=args.formats,
            code_formats=args.code,
            languages=args.languages,
            offline=args.offline,
        )
    result = run_figure_agent(request)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
