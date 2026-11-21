from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from patent_agent.adapters.cnipa import PriorArtRecord, SearchResult, normalize_hit
from patent_agent.errors import ParseError
from patent_agent.utils import sha256_file, utc_now, write_json


BENCHMARK_SCHEMA = "cnipa_recall_benchmark_v1"
FIXTURE_SCHEMA = "cnipa_recall_fixture_v1"
REPORT_SCHEMA = "cnipa_recall_report_v1"
COMPLETED_SEARCH_STATUSES = {"success", "zero_results"}
PUBLICATION_NUMBER = re.compile(r"^CN\d{6,12}[A-Z]\d?$")


class SearchAdapter(Protocol):
    def search(self, query: str) -> SearchResult: ...


def normalize_publication_number(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ParseError(f"{label} is not readable JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ParseError(f"{label} root must be a JSON object")
    return payload


def load_benchmark(path: Path) -> dict[str, Any]:
    payload = _load_json_object(path, label="CNIPA benchmark")
    if payload.get("schema_version") != BENCHMARK_SCHEMA:
        raise ParseError(f"CNIPA benchmark schema_version must be {BENCHMARK_SCHEMA}")
    if not str(payload.get("benchmark_id", "")).strip():
        raise ParseError("CNIPA benchmark benchmark_id must be non-empty")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ParseError("CNIPA benchmark cases must be a non-empty array")

    case_ids: set[str] = set()
    strategy_shape: tuple[str, ...] | None = None
    for case in cases:
        if not isinstance(case, dict):
            raise ParseError("CNIPA benchmark case must be an object")
        case_id = str(case.get("case_id", "")).strip()
        if not case_id or case_id in case_ids:
            raise ParseError("CNIPA benchmark case_id values must be non-empty and unique")
        case_ids.add(case_id)

        patents = case.get("relevant_patents")
        if not isinstance(patents, list) or not patents:
            raise ParseError(f"benchmark case {case_id} must contain relevant_patents")
        publication_numbers: set[str] = set()
        for patent in patents:
            if not isinstance(patent, dict):
                raise ParseError(f"benchmark case {case_id} patent must be an object")
            number = normalize_publication_number(patent.get("publication_number"))
            if not PUBLICATION_NUMBER.fullmatch(number) or number in publication_numbers:
                raise ParseError(
                    f"benchmark case {case_id} has invalid or duplicate publication_number"
                )
            publication_numbers.add(number)
            if not str(patent.get("title", "")).strip():
                raise ParseError(f"benchmark patent {number} must contain a title")
            if not str(patent.get("judgment_basis", "")).strip():
                raise ParseError(f"benchmark patent {number} must contain judgment_basis")
            source_url = str(patent.get("source_url", "")).strip()
            if not source_url.startswith("http"):
                raise ParseError(f"benchmark patent {number} must contain a public source_url")

        strategies = case.get("strategies")
        if not isinstance(strategies, list) or not strategies:
            raise ParseError(f"benchmark case {case_id} must contain strategies")
        strategy_ids: list[str] = []
        for strategy in strategies:
            if not isinstance(strategy, dict):
                raise ParseError(f"benchmark case {case_id} strategy must be an object")
            strategy_id = str(strategy.get("strategy_id", "")).strip()
            queries = strategy.get("queries")
            if not strategy_id or strategy_id in strategy_ids:
                raise ParseError(
                    f"benchmark case {case_id} strategy_id values must be non-empty and unique"
                )
            if not isinstance(queries, list) or not queries:
                raise ParseError(
                    f"benchmark case {case_id} strategy {strategy_id} must contain queries"
                )
            for query in queries:
                normalized = " ".join(str(query).split()).strip()
                if not normalized or len(normalized) > 80:
                    raise ParseError(
                        f"benchmark query in {case_id}/{strategy_id} must contain 1-80 characters"
                    )
            strategy_ids.append(strategy_id)
        current_shape = tuple(strategy_ids)
        if not current_shape or current_shape[0] != "baseline":
            raise ParseError("every benchmark case must list baseline as its first strategy")
        if strategy_shape is None:
            strategy_shape = current_shape
        elif current_shape != strategy_shape:
            raise ParseError("every benchmark case must use the same ordered strategy_id set")

    unsupported = payload.get("unsupported_strategies", [])
    if not isinstance(unsupported, list):
        raise ParseError("unsupported_strategies must be an array")
    for row in unsupported:
        if (
            not isinstance(row, dict)
            or not str(row.get("strategy_id", "")).strip()
            or not str(row.get("reason", "")).strip()
        ):
            raise ParseError(
                "each unsupported strategy must contain strategy_id and reason"
            )
    return payload


def load_fixture_results(path: Path) -> dict[str, list[dict[str, Any]]]:
    payload = _load_json_object(path, label="CNIPA benchmark fixture")
    if payload.get("schema_version") != FIXTURE_SCHEMA:
        raise ParseError(f"CNIPA fixture schema_version must be {FIXTURE_SCHEMA}")
    raw_results = payload.get("query_results")
    if not isinstance(raw_results, dict):
        raise ParseError("CNIPA benchmark fixture query_results must be an object")
    results: dict[str, list[dict[str, Any]]] = {}
    for query, records in raw_results.items():
        normalized_query = " ".join(str(query).split()).strip()
        if not normalized_query or not isinstance(records, list):
            raise ParseError("fixture queries must be non-empty and map to record arrays")
        normalized_records: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                raise ParseError(f"fixture query {normalized_query} contains a non-object record")
            number = normalize_publication_number(record.get("publication_number"))
            if not PUBLICATION_NUMBER.fullmatch(number):
                raise ParseError(
                    f"fixture query {normalized_query} contains an invalid publication_number"
                )
            normalized_records.append(record)
        results[normalized_query] = normalized_records
    return results


class BenchmarkFixtureAdapter:
    def __init__(self, query_results: dict[str, list[dict[str, Any]]]):
        self.query_results = query_results

    def search(self, query: str) -> SearchResult:
        query = " ".join(query.split()).strip()
        if query not in self.query_results:
            raise ParseError(
                f"benchmark fixture has no explicit result entry for query: {query}"
            )
        raw = self.query_results[query]
        records: list[PriorArtRecord] = []
        for row in raw:
            labeled = dict(row)
            labeled["source_name"] = "Benchmark fixture snapshot (not live CNIPA)"
            records.append(
                normalize_hit(
                    labeled,
                    retrieved_at="2026-07-20T00:00:00Z",
                )
            )
        return SearchResult(
            query=query,
            status="success" if records else "zero_results",
            result_count=len(records),
            records=records,
            error_type=None,
            error_message=None,
            elapsed_seconds=0.0,
        )


def _public_record(record: PriorArtRecord, *, rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "publication_number": record.publication_number,
        "title": record.title,
        "source_url": record.source_url,
        "source_name": record.source_name,
    }


def _run_strategy(
    adapter: SearchAdapter,
    strategy: dict[str, Any],
    expected_numbers: set[str],
    *,
    top_k: int,
) -> dict[str, Any]:
    query_results: list[dict[str, Any]] = []
    pooled_records: list[dict[str, Any]] = []
    pooled_numbers: list[str] = []
    seen: set[str] = set()
    total_seconds = 0.0

    for query in strategy["queries"]:
        result = adapter.search(query)
        total_seconds += result.elapsed_seconds
        selected = result.records[:top_k]
        public_records = [
            _public_record(record, rank=index)
            for index, record in enumerate(selected, start=1)
        ]
        query_results.append(
            {
                "query": result.query,
                "status": result.status,
                "result_count": result.result_count,
                "top_k_records": public_records,
                "elapsed_seconds": result.elapsed_seconds,
                "error_type": result.error_type,
                "error_message": result.error_message,
            }
        )
        for public_record in public_records:
            number = normalize_publication_number(public_record["publication_number"])
            if not number or number in seen:
                continue
            seen.add(number)
            pooled_numbers.append(number)
            pooled_records.append(public_record)

    matched = [number for number in pooled_numbers if number in expected_numbers]
    missing = sorted(expected_numbers.difference(matched))
    status_counts = Counter(row["status"] for row in query_results)
    completed_count = sum(
        count
        for status, count in status_counts.items()
        if status in COMPLETED_SEARCH_STATUSES
    )
    return {
        "strategy_id": strategy["strategy_id"],
        "strategy_family": strategy.get("strategy_family"),
        "query_count": len(query_results),
        "completed_query_count": completed_count,
        "external_complete": completed_count == len(query_results),
        "status_counts": dict(sorted(status_counts.items())),
        "elapsed_seconds": round(total_seconds, 3),
        "retrieved_unique_count": len(pooled_numbers),
        "retrieved_publication_numbers": pooled_numbers,
        "matched_publication_numbers": matched,
        "missing_publication_numbers": missing,
        "recall_at_k": round(len(matched) / len(expected_numbers), 6),
        "query_results": query_results,
        "pooled_top_k_records": pooled_records,
    }


def run_benchmark(
    benchmark_path: Path,
    adapter: SearchAdapter,
    *,
    mode: str,
    top_k: int = 3,
    fixture_path: Path | None = None,
    output_path: Path | None = None,
    selected_strategy_ids: set[str] | None = None,
) -> dict[str, Any]:
    if mode not in {"fixture", "real_cnipa"}:
        raise ParseError("benchmark mode must be fixture or real_cnipa")
    if mode == "fixture" and fixture_path is None:
        raise ParseError("fixture benchmark requires fixture_path for evidence hashing")
    if top_k < 1 or top_k > 20:
        raise ParseError("benchmark top_k must be between 1 and 20")
    if output_path is not None and output_path.exists():
        raise ParseError(f"benchmark output already exists: {output_path}")
    benchmark = load_benchmark(benchmark_path)
    all_strategy_ids = [
        row["strategy_id"] for row in benchmark["cases"][0]["strategies"]
    ]
    if selected_strategy_ids:
        unknown = selected_strategy_ids.difference(all_strategy_ids)
        if unknown:
            raise ParseError(
                "unknown benchmark strategy_id: " + ", ".join(sorted(unknown))
            )
        strategy_ids = [
            strategy_id
            for strategy_id in all_strategy_ids
            if strategy_id in selected_strategy_ids
        ]
    else:
        strategy_ids = all_strategy_ids
    cases: list[dict[str, Any]] = []
    for case in benchmark["cases"]:
        expected_numbers = {
            normalize_publication_number(row["publication_number"])
            for row in case["relevant_patents"]
        }
        strategy_results = [
            _run_strategy(adapter, strategy, expected_numbers, top_k=top_k)
            for strategy in case["strategies"]
            if strategy["strategy_id"] in strategy_ids
        ]
        baseline_row = next(
            (
                row
                for row in strategy_results
                if row["strategy_id"] == "baseline"
            ),
            None,
        )
        baseline = (
            baseline_row["recall_at_k"]
            if baseline_row is not None
            else None
        )
        for result in strategy_results:
            result["recall_delta_vs_baseline"] = (
                round(result["recall_at_k"] - baseline, 6)
                if baseline is not None
                else None
            )
        cases.append(
            {
                "case_id": case["case_id"],
                "topic": case["topic"],
                "expected_relevant_count": len(expected_numbers),
                "expected_publication_numbers": sorted(expected_numbers),
                "strategies": strategy_results,
            }
        )

    aggregates: list[dict[str, Any]] = []
    for strategy_id in strategy_ids:
        rows = [
            next(
                strategy
                for strategy in case["strategies"]
                if strategy["strategy_id"] == strategy_id
            )
            for case in cases
        ]
        expected_total = sum(case["expected_relevant_count"] for case in cases)
        matched_total = sum(len(row["matched_publication_numbers"]) for row in rows)
        aggregates.append(
            {
                "strategy_id": strategy_id,
                "case_count": len(rows),
                "query_count": sum(row["query_count"] for row in rows),
                "completed_query_count": sum(
                    row["completed_query_count"] for row in rows
                ),
                "external_complete": all(row["external_complete"] for row in rows),
                "elapsed_seconds": round(
                    sum(row["elapsed_seconds"] for row in rows),
                    3,
                ),
                "macro_recall_at_k": round(
                    sum(row["recall_at_k"] for row in rows) / len(rows),
                    6,
                ),
                "micro_recall_at_k": round(matched_total / expected_total, 6),
                "matched_relevant_count": matched_total,
                "expected_relevant_count": expected_total,
            }
        )
    baseline_aggregate = next(
        (
            aggregate
            for aggregate in aggregates
            if aggregate["strategy_id"] == "baseline"
        ),
        None,
    )
    for aggregate in aggregates:
        aggregate["macro_recall_delta_vs_baseline"] = (
            round(
                aggregate["macro_recall_at_k"]
                - baseline_aggregate["macro_recall_at_k"],
                6,
            )
            if baseline_aggregate is not None
            else None
        )
        aggregate["micro_recall_delta_vs_baseline"] = (
            round(
                aggregate["micro_recall_at_k"]
                - baseline_aggregate["micro_recall_at_k"],
                6,
            )
            if baseline_aggregate is not None
            else None
        )

    external_complete = all(row["external_complete"] for row in aggregates)
    measurement_status = (
        "fixture_contract_only"
        if mode == "fixture"
        else "real_complete"
        if external_complete
        else "real_partial"
    )
    report = {
        "schema_version": REPORT_SCHEMA,
        "benchmark_id": benchmark["benchmark_id"],
        "benchmark_sha256": sha256_file(benchmark_path),
        "fixture_sha256": (
            sha256_file(fixture_path)
            if mode == "fixture" and fixture_path is not None
            else None
        ),
        "generated_at": utc_now(),
        "mode": mode,
        "measurement_status": measurement_status,
        "top_k_per_query": top_k,
        "selected_strategy_ids": strategy_ids,
        "case_count": len(cases),
        "known_relevant_patent_count": sum(
            case["expected_relevant_count"] for case in cases
        ),
        "external_complete": external_complete,
        "aggregates": aggregates,
        "cases": cases,
        "unsupported_strategies": benchmark.get("unsupported_strategies", []),
        "interpretation": {
            "fixture_is_real_recall_measurement": False,
            "automatic_novelty_or_inventiveness_conclusion": False,
            "result_count_is_not_recall": True,
            "real_partial_is_lower_bound_only": mode == "real_cnipa"
            and not external_complete,
        },
    }
    if output_path is not None:
        write_json(output_path, report)
        report["output_path"] = str(output_path)
    return report


def default_report_path(outputs_dir: Path, *, mode: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return outputs_dir / "cnipa_benchmarks" / f"{timestamp}-{mode}.json"
