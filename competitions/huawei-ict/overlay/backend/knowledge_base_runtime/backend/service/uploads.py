from __future__ import annotations

import uuid
import hashlib
import threading
from pathlib import Path
from typing import Any

from knowledge_base_runtime.backend.config.settings import DB_BACKEND, PDF_DIR, UPLOAD_DIR
from knowledge_base_runtime.backend.dao.database import get_db, utc_now
from knowledge_base_runtime.backend.service.audit import record_audit_log
from knowledge_base_runtime.backend.service.chunking import extract_fulltext_from_pdf_bytes, extract_text_from_pdf_bytes, replace_chunks, upsert_paper_fulltext
from knowledge_base_runtime.backend.utils.common import as_list, dumps, loads_list
from knowledge_base_runtime.backend.service.metadata import upsert_user_paper
from knowledge_base_runtime.backend.service.pdf_storage import remove_own_pdf, store_pdf, validate_paper_id

_SQLITE_UPLOAD_LOCK = threading.Lock()


def create_upload_task(filename: str, data: bytes, user_id: str, ip: str | None = None) -> dict[str, Any]:
    task_id = f"upload_{uuid.uuid4().hex[:12]}"
    suffix = Path(filename or "paper.pdf").suffix or ".pdf"
    temp_key = f"{task_id}{suffix}"
    temp_path = UPLOAD_DIR / temp_key
    temp_path.write_bytes(data)

    guessed_title = Path(filename or "未命名论文").stem
    text = extract_text_from_pdf_bytes(data)
    if text:
        first_words = " ".join(text.split()[:18])
        guessed_title = first_words[:120] or guessed_title

    now = utc_now()
    with get_db() as db:
        db.execute(
            """
            INSERT INTO upload_tasks(task_id, user_id, status, temp_key, title, authors, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, user_id, "Awaiting Confirm", temp_key, guessed_title, "[]", now, now),
        )
        record_audit_log(
            db,
            operate_user_id=user_id,
            user_ip=ip,
            operate_type="PAPER_INGEST",
            operate_sub_type="PDF_AUTO_PARSE",
            target_resource_type="upload_task",
            target_resource_id=task_id,
            resource_title=filename,
            operate_content={"filename": filename, "bytes": len(data), "candidate_title": guessed_title},
            is_system_op=False,
        )
    return {"task_id": task_id, "status": "Awaiting Confirm", "candidate": {"title": guessed_title, "authors": []}}


def confirm_upload(payload: dict[str, Any], user_id: str, ip: str | None = None) -> dict[str, Any]:
    task_id = str(payload.get("task_id") or "")
    if not task_id:
        raise ValueError("task_id is required")
    if DB_BACKEND == "postgresql":
        # A transaction-scoped advisory lock serializes the same task without
        # locking its row or persisting a Processing status. PostgreSQL releases
        # it automatically if the worker or connection stops unexpectedly.
        lock_id = int.from_bytes(hashlib.sha256(task_id.encode("utf-8")).digest()[:8], "big", signed=True)
        with get_db() as db:
            lock = db.execute("SELECT pg_try_advisory_xact_lock(?) AS acquired", (lock_id,)).fetchone()
            if not lock["acquired"]:
                raise ValueError("upload task is already being processed")
            task = _load_confirmable_task(db, task_id, user_id)
            return _run_confirm(payload, task, task_id, user_id, ip)
    # SQLite has a database-wide writer lock; do not keep its read transaction
    # open while the existing upload pipeline writes on other connections.
    # The supported local runtime is a single worker with multiple threads.
    with _SQLITE_UPLOAD_LOCK:
        with get_db() as db:
            task = _load_confirmable_task(db, task_id, user_id)
        return _run_confirm(payload, task, task_id, user_id, ip)


def _load_confirmable_task(db, task_id: str, user_id: str):
    task = db.execute("SELECT * FROM upload_tasks WHERE task_id = ? AND user_id = ?", (task_id, user_id)).fetchone()
    if task is None:
        raise ValueError("upload task not found")
    if task["status"] != "Awaiting Confirm":
        raise ValueError("upload task is already being processed or completed")
    return task


def _run_confirm(payload, task, task_id, user_id, ip):
    if not bool(payload.get("confirm", True)):
        _update_task(task_id, "Failed", error="user rejected candidate metadata")
        return {"task_id": task_id, "status": "Failed"}

    try:
        return _finish_upload(payload, task, task_id, user_id, ip)
    except Exception:
        _update_task(task_id, "Failed", error="上传处理失败，请重新上传")
        raise


def _finish_upload(payload: dict[str, Any], task, task_id: str, user_id: str, ip: str | None) -> dict[str, Any]:

    title = str(payload.get("title") or task["title"] or "用户上传论文").strip()
    authors = as_list(payload.get("authors") or loads_list(task["authors"]))
    paper_id = validate_paper_id(str(payload.get("paper_id") or f"user_{uuid.uuid4().hex[:16]}"))
    temp_path = UPLOAD_DIR / task["temp_key"]
    # A random internal filename means an orphan left by a process crash
    # cannot block a future retry of the same paper ID or overwrite old files.
    pdf_key = f"upload_{uuid.uuid4().hex}.pdf"
    pdf_path = PDF_DIR / pdf_key

    paper_item = {
        "id": paper_id,
        "title": title,
        "Author": authors,
        "abstract": payload.get("abstract") or "",
        "publish_year": payload.get("publish_year"),
        "keywords": payload.get("keywords") or [],
        "minio_pdf_key": f"/pdfs/{pdf_key}",
        "parse_status": 1,
        "source": "user_upload",
    }
    published = []
    def publish_file():
        result = store_pdf(temp_path, paper_id, pdf_key)
        published.append(result)
        return result
    try:
        upsert_user_paper(paper_item, user_id=user_id, ip=ip,
                          publish_file=publish_file, cleanup_file=remove_own_pdf)
    finally:
        for item in published:
            item.close()
    extraction = extract_fulltext_from_pdf_bytes(pdf_path.read_bytes())
    upsert_paper_fulltext(
        paper_id,
        minio_pdf_key=paper_item["minio_pdf_key"],
        raw_text=extraction.raw_text,
        clean_text=extraction.clean_text,
        extraction_method=extraction.extraction_method,
        clean_strategy=extraction.clean_strategy,
        mojibake_hits=extraction.mojibake_hits,
    )
    strategy = str(payload.get("strategy") or "fixed_1024_200")
    chunk_count, warnings = replace_chunks(paper_id, extraction.clean_text or title, strategy=strategy)
    _update_task(task_id, "Confirmed", paper_id=paper_id, title=title, authors=authors)
    return {"task_id": task_id, "status": "Confirmed", "paper_id": paper_id, "chunk_count": chunk_count, "warnings": warnings}


def list_upload_tasks(user_id: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM upload_tasks"
    params: list[Any] = []
    if user_id:
        sql += " WHERE user_id = ?"
        params.append(user_id)
    sql += " ORDER BY created_at DESC"
    with get_db() as db:
        rows = db.execute(sql, params).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["authors"] = loads_list(item.get("authors"))
        result.append(item)
    return result


def get_upload_task(task_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    sql = "SELECT * FROM upload_tasks WHERE task_id = ?"
    params: list[Any] = [task_id]
    if user_id:
        sql += " AND user_id = ?"
        params.append(user_id)
    with get_db() as db:
        row = db.execute(sql, params).fetchone()
    if row is None:
        return None
    item = dict(row)
    authors = loads_list(item.get("authors"))
    item["authors"] = authors
    item["meta"] = {"title": item.get("title") or "", "authors": ", ".join(str(author) for author in authors)}
    return item


def _update_task(
    task_id: str,
    status: str,
    *,
    paper_id: str | None = None,
    title: str | None = None,
    authors: list | None = None,
    error: str | None = None,
) -> None:
    with get_db() as db:
        db.execute(
            """
            UPDATE upload_tasks
            SET status = ?,
                paper_id = COALESCE(?, paper_id),
                title = COALESCE(?, title),
                authors = COALESCE(?, authors),
                error = ?,
                updated_at = ?
            WHERE task_id = ?
            """,
            (status, paper_id, title, dumps(authors) if authors is not None else None, error, utc_now(), task_id),
        )
