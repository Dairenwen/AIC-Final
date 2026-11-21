from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parent
SRC_ROOT = CORE_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from innovation_mining import InnovationOrchestrator, InnovationRequest
from innovation_mining.models import utc_now_iso
from innovation_mining.utils import slugify, split_list


def parse_constraints(value: str | None) -> dict:
    if not value:
        return {}
    candidate = value.strip()
    if not candidate:
        return {}
    path = Path(candidate)
    if path.exists() and path.is_file():
        candidate = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--constraints-json is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("--constraints-json must be a JSON object.")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Innovation Mining agent.")
    parser.add_argument("--domain", "--research-domain", dest="research_domain", required=True)
    parser.add_argument("--keyword", action="append", default=[], help="Repeatable keyword.")
    parser.add_argument("--keywords", default="", help="Comma/newline separated keywords.")
    parser.add_argument("--seed-idea", action="append", default=[], help="Repeatable seed idea.")
    parser.add_argument("--seed-ideas", default="", help="Comma/newline separated seed ideas.")
    parser.add_argument("--time-range", default=None, help='Optional paper year range, e.g. "2020-2026".')
    parser.add_argument("--mode", choices=["full", "evaluate", "expand"], default="full")
    parser.add_argument("--constraints-json", default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--language", default="zh")
    parser.add_argument("--additional-context", default="")
    parser.add_argument("--corpus", default="data/raw")
    parser.add_argument("--max-documents", type=int, default=80)
    parser.add_argument("--out", default="data/innovation_runs")
    parser.add_argument("--print-json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    keywords = split_list(args.keyword + split_list(args.keywords))
    seed_ideas = split_list(args.seed_idea + split_list(args.seed_ideas))
    constraints = parse_constraints(args.constraints_json)

    request = InnovationRequest(
        research_domain=args.research_domain,
        keywords=keywords,
        seed_ideas=seed_ideas,
        time_range=args.time_range,
        mode=args.mode,
        constraints=constraints,
        top_k=max(1, args.top_k),
        language=args.language,
        additional_context=args.additional_context,
        corpus_dir=args.corpus,
        max_documents=max(10, args.max_documents),
    )
    result = InnovationOrchestrator().run(request)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = utc_now_iso().replace(":", "").replace("-", "").replace("Z", "Z")
    out_path = out_dir / f"{stamp}_{slugify(args.research_domain)}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    top_titles = [item["title"] for item in result.get("innovations", [])[: request.top_k]]
    print(f"Innovation Mining agent completed: {len(top_titles)} proposal(s)")
    print(f"domain: {args.research_domain}")
    print(f"mode: {args.mode}")
    print(f"documents: {result.get('metadata', {}).get('document_count', 0)}")
    for index, title in enumerate(top_titles, 1):
        print(f"{index}. {title}")
    try:
        rel_path = out_path.resolve().relative_to(Path.cwd().resolve()).as_posix()
        print(f"RESULT_JSON_REL={rel_path}")
    except ValueError:
        pass
    print(f"RESULT_JSON={out_path.resolve()}")

    if args.print_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
