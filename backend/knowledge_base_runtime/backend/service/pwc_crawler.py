from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from knowledge_base_runtime.backend.dao.database import get_db, utc_now
from knowledge_base_runtime.backend.utils.common import dumps
from knowledge_base_runtime.backend.service.metadata import ingest_papers


def iter_crawled_papers(input_path: Path) -> list[dict[str, Any]]:
    files = sorted(input_path.glob("*.json")) if input_path.is_dir() else [input_path]
    papers: list[dict[str, Any]] = []
    for file_path in files:
        if file_path.name == "_index.json":
            continue
        data = json.loads(file_path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, list):
            continue
        for item in data:
            if isinstance(item, dict):
                paper = normalize_pwc_paper(item, source_file=str(file_path))
                if paper is not None:
                    papers.append(paper)
    return papers


def normalize_pwc_paper(item: dict[str, Any], source_file: str | None = None) -> dict[str, Any] | None:
    if item.get("error") and not item.get("title"):
        return None
    paper_id = _clean_text(item.get("id") or item.get("arxiv_id") or item.get("source_url"))
    title = _clean_text(item.get("title"))
    if not paper_id and not title:
        return None

    tasks = _as_text_list(item.get("tasks"))
    methods = _as_text_list(item.get("methods"))
    source_url = _clean_text(item.get("source_url") or item.get("paper_url") or item.get("source_page"))
    normalized = {
        "id": paper_id,
        "title": title,
        "publish_venue": _clean_text(item.get("publish_venue") or item.get("venue") or item.get("conference")),
        "publish_year": item.get("publish_year") or item.get("year"),
        "abstract": _clean_text(item.get("abstract")),
        "key_words": _as_text_list(item.get("key_words") or item.get("keywords")),
        "related_papers": _as_text_list(item.get("related_papers")),
        "tasks": tasks,
        "methods": methods,
        "research_area": _clean_text(item.get("research_area")),
        "subfield": _clean_text(item.get("subfield")),
        "task_name": _clean_text(item.get("task_name")) or (tasks[0] if tasks else None),
        "Author": item.get("Author") or item.get("author") or item.get("authors"),
        "pdf_url": _clean_text(item.get("pdf_url")),
        "github_url": _clean_text(item.get("github_url")),
        "arxiv_url": _clean_text(item.get("arxiv_url")),
        "project_url": _clean_text(item.get("project_url")),
        "source_url": source_url,
        "paper_url": source_url,
        "source_page": source_url,
        "arxiv_id": _clean_text(item.get("arxiv_id")),
        "citations": item.get("citations"),
        "citation_count": item.get("citation_count") or item.get("citations"),
        "upload_time": item.get("upload_time"),
        "parse_finish_time": item.get("parse_finish_time"),
        "chunk_gen_time": item.get("chunk_gen_time"),
        "vector_index_time": item.get("vector_index_time"),
        "last_refresh_time": item.get("last_refresh_time"),
        "last_access_time": item.get("last_access_time"),
        "delete_time": item.get("delete_time"),
        "metadata_updated_at": item.get("metadata_updated_at") or item.get("update_time") or utc_now(),
        "source_file": source_file,
        "raw_metadata": item,
        "source": "pwc_crawler",
    }
    return normalized


def import_crawled_papers(
    input_path: Path,
    *,
    task_name: str = "pwc_manual_import",
    user_id: str = "system",
    log_file: str | None = None,
) -> dict[str, Any]:
    start_time = utc_now()
    papers: list[dict[str, Any]] = []
    try:
        papers = iter_crawled_papers(input_path)
        result = ingest_papers(papers, user_id=user_id)
    except Exception:
        end_time = utc_now()
        record_task_run(
            task_name=task_name,
            task_start_time=start_time,
            task_end_time=end_time,
            add_paper_count=0,
            skip_paper_count=0,
            exception_create_time=end_time,
            crawl_exit_code=0,
            import_exit_code=1,
            log_file=log_file,
            summary={"input": str(input_path), "loaded": len(papers), "error": "import failed"},
        )
        raise

    end_time = utc_now()
    run_id = record_task_run(
        task_name=task_name,
        task_start_time=start_time,
        task_end_time=end_time,
        add_paper_count=int(result.get("inserted") or 0),
        skip_paper_count=int(result.get("skipped") or 0),
        exception_create_time=None,
        crawl_exit_code=0,
        import_exit_code=0,
        log_file=log_file,
        summary={"input": str(input_path), **result},
    )
    return {"task_run_id": run_id, "total": len(papers), **result}


def record_task_run(
    *,
    task_name: str,
    task_start_time: str,
    task_end_time: str,
    add_paper_count: int = 0,
    skip_paper_count: int = 0,
    exception_create_time: str | None = None,
    crawl_exit_code: int | None = None,
    import_exit_code: int | None = None,
    log_file: str | None = None,
    summary: dict[str, Any] | None = None,
) -> int:
    with get_db() as db:
        cur = db.execute(
            """
            INSERT INTO crawler_task_runs(
                task_name,
                task_start_time,
                task_end_time,
                add_paper_count,
                skip_paper_count,
                exception_create_time,
                crawl_exit_code,
                import_exit_code,
                log_file,
                summary,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_name,
                task_start_time,
                task_end_time,
                add_paper_count,
                skip_paper_count,
                exception_create_time,
                crawl_exit_code,
                import_exit_code,
                log_file,
                dumps(summary or {}),
                utc_now(),
            ),
        )
        return int(getattr(cur, "lastrowid", 0) or _last_insert_id(db))


def list_task_runs(page: int = 1, size: int = 20) -> dict[str, Any]:
    page = max(page, 1)
    size = min(max(size, 1), 100)
    offset = (page - 1) * size
    with get_db() as db:
        total = db.execute("SELECT COUNT(*) AS c FROM crawler_task_runs").fetchone()["c"]
        rows = db.execute(
            """
            SELECT *
            FROM crawler_task_runs
            ORDER BY task_start_time DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (size, offset),
        ).fetchall()
    return {"total": total, "page": page, "size": size, "list": [dict(row) for row in rows]}


def _last_insert_id(db: Any) -> int:
    row = db.execute("SELECT id FROM crawler_task_runs ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_text_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).replace(";", ",").split(",") if part.strip()]
