from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any


def run_concurrently(
    tasks: Mapping[str, Callable[[], Any]],
    *,
    max_workers: int = 8,
) -> dict[str, Any]:
    """Run independent blocking tasks concurrently and return results in input order."""

    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    if not tasks:
        return {}
    worker_count = min(max_workers, len(tasks))
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="paper-reading-model",
    ) as executor:
        futures = {name: executor.submit(task) for name, task in tasks.items()}
        return {name: futures[name].result() for name in tasks}


def run_concurrently_flow_first(
    tasks: Mapping[str, Callable[[], Any]],
    *,
    max_workers: int = 8,
) -> tuple[dict[str, Any], dict[str, Exception]]:
    """Run independent tasks while preserving successful sibling results."""

    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    if not tasks:
        return {}, {}
    worker_count = min(max_workers, len(tasks))
    results: dict[str, Any] = {}
    failures: dict[str, Exception] = {}
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="paper-reading-model",
    ) as executor:
        futures = {name: executor.submit(task) for name, task in tasks.items()}
        for name in tasks:
            try:
                results[name] = futures[name].result()
            except Exception as exc:
                failures[name] = exc
    return results, failures
