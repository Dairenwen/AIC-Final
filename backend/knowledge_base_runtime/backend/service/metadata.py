from __future__ import annotations

from typing import Any

from knowledge_base_runtime.backend.dao.database import get_db, utc_now
from knowledge_base_runtime.backend.service.audit import list_audit_logs as query_audit_logs
from knowledge_base_runtime.backend.service.audit import record_audit_log
from knowledge_base_runtime.backend.utils.common import as_list, dumps, loads_list
from knowledge_base_runtime.backend.client.retrieval_backends import delete_paper_elasticsearch, index_paper_elasticsearch


PAPER_FIELDS = [
    "id",
    "publish_venue",
    "title",
    "publish_year",
    "abstract",
    "keywords",
    "pdf_url",
    "arxiv_url",
    "github_url",
    "project_url",
    "source_url",
    "arxiv_id",
    "citations",
    "related_papers",
    "tasks",
    "methods",
    "research_area",
    "subfield",
    "task_name",
    "paper_url",
    "source_page",
    "citation_count",
    "author",
    "minio_pdf_key",
    "parse_status",
    "metadata_updated_at",
    "source_file",
    "raw_metadata",
    "source",
    "created_at",
    "updated_at",
]


def normalize_paper(item: dict[str, Any]) -> dict[str, Any]:
    paper_id = str(item.get("id") or item.get("arxiv_id") or item.get("doi") or "").strip()
    title = str(item.get("title") or "").strip()
    if not paper_id:
        if not title:
            raise ValueError("paper item requires id or title")
        paper_id = title.lower().replace(" ", "-")[:80]
    if not title:
        raise ValueError(f"paper {paper_id} requires title")

    now = utc_now()
    author = as_list(item.get("Author", item.get("author", item.get("authors"))))
    keywords = as_list(item.get("key_words", item.get("keywords")))
    related = as_list(item.get("related_papers"))
    tasks = as_list(item.get("tasks"))
    methods = as_list(item.get("methods"))
    citation_count = _int_or_none(item.get("citation_count", item.get("citations"))) or 0
    source_url = item.get("source_url") or item.get("paper_url") or item.get("source_page")
    raw_metadata = item.get("raw_metadata")
    if raw_metadata is None:
        raw_metadata = item.get("raw")
    if raw_metadata is None and item.get("source") == "pwc_crawler":
        raw_metadata = item
    return {
        "id": paper_id,
        "publish_venue": item.get("publish_venue") or item.get("venue"),
        "title": title,
        "publish_year": _int_or_none(item.get("publish_year") or item.get("year")),
        "abstract": item.get("abstract") or "",
        "keywords": dumps(keywords),
        "pdf_url": item.get("pdf_url"),
        "arxiv_url": item.get("arxiv_url"),
        "github_url": item.get("github_url"),
        "project_url": item.get("project_url"),
        "source_url": source_url,
        "arxiv_id": item.get("arxiv_id"),
        "citations": _int_or_none(item.get("citations")),
        "related_papers": dumps(related),
        "tasks": dumps(tasks),
        "methods": dumps(methods),
        "research_area": item.get("research_area"),
        "subfield": item.get("subfield"),
        "task_name": item.get("task_name") or (tasks[0] if tasks else None),
        "paper_url": item.get("paper_url") or source_url,
        "source_page": item.get("source_page") or source_url,
        "citation_count": citation_count,
        "author": dumps(author),
        "minio_pdf_key": item.get("minio_pdf_key"),
        "parse_status": _normalize_parse_status(item),
        "metadata_updated_at": item.get("metadata_updated_at") or now,
        "source_file": item.get("source_file"),
        "raw_metadata": dumps(raw_metadata) if raw_metadata is not None else None,
        "source": item.get("source") or "crawler",
        "created_at": now,
        "updated_at": now,
    }


def ingest_papers(items: list[dict[str, Any]], user_id: str = "system", ip: str | None = None) -> dict[str, Any]:
    inserted = 0
    skipped = 0
    errors: list[dict[str, Any]] = []
    with get_db() as db:
        for index, item in enumerate(items):
            try:
                paper = normalize_paper(item)
            except ValueError as exc:
                errors.append({"index": index, "error": str(exc)})
                continue
            placeholders = ", ".join("?" for _ in PAPER_FIELDS)
            columns = ", ".join(PAPER_FIELDS)
            values = [paper[field] for field in PAPER_FIELDS]
            cur = db.execute(
                f"INSERT OR IGNORE INTO papers ({columns}) VALUES ({placeholders})",
                values,
            )
            if cur.rowcount:
                inserted += 1
                _upsert_search_index(db, paper)
                index_paper_elasticsearch(paper)
            else:
                skipped += 1
        record_audit_log(
            db,
            operate_user_id=user_id,
            user_ip=ip,
            operate_type="PAPER_INGEST",
            operate_sub_type="CRAWLER_METADATA_INGEST",
            target_resource_type="crawler_task",
            resource_title="文献元数据入库",
            operate_content={"inserted": inserted, "skipped": skipped, "errors": errors},
            is_system_op=user_id == "system",
        )
    return {"inserted": inserted, "skipped": skipped, "errors": errors}


def upsert_user_paper(item: dict[str, Any], user_id: str, ip: str | None = None) -> str:
    paper = normalize_paper({**item, "source": item.get("source") or "user_upload"})
    with get_db() as db:
        placeholders = ", ".join("?" for _ in PAPER_FIELDS)
        columns = ", ".join(PAPER_FIELDS)
        values = [paper[field] for field in PAPER_FIELDS]
        db.execute(f"INSERT OR IGNORE INTO papers ({columns}) VALUES ({placeholders})", values)
        _upsert_search_index(db, paper)
        index_paper_elasticsearch(paper)
        record_audit_log(
            db,
            operate_user_id=user_id,
            user_ip=ip,
            operate_type="PAPER_INGEST",
            operate_sub_type="PAPER_CREATE",
            target_resource_type="paper",
            target_resource_id=paper["id"],
            resource_title=paper.get("title"),
            operate_content={"source": paper.get("source"), "title": paper.get("title")},
            is_system_op=user_id == "system",
        )
    return paper["id"]


def list_papers(
    page: int = 1,
    size: int = 20,
    search: str | None = None,
    parse_status: int | None = None,
) -> dict[str, Any]:
    page = max(page, 1)
    size = min(max(size, 1), 100)
    offset = (page - 1) * size
    where = ["1=1"]
    params: list[Any] = []
    if search:
        where.append("(title LIKE ? OR id LIKE ? OR abstract LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    if parse_status is not None:
        where.append("parse_status = ?")
        params.append(parse_status)
    where_sql = " AND ".join(where)
    with get_db() as db:
        total = db.execute(f"SELECT COUNT(*) AS c FROM papers WHERE {where_sql}", params).fetchone()["c"]
        rows = db.execute(
            f"SELECT * FROM papers WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*params, size, offset],
        ).fetchall()
    return {"total": total, "page": page, "size": size, "list": [serialize_paper(dict(row)) for row in rows]}


def get_paper(paper_id: str) -> dict[str, Any] | None:
    with get_db() as db:
        row = db.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    return serialize_paper(dict(row)) if row else None


def get_stats() -> dict[str, Any]:
    with get_db() as db:
        total = db.execute("SELECT COUNT(*) AS c FROM papers").fetchone()["c"]
        parsed = db.execute("SELECT COUNT(*) AS c FROM papers WHERE parse_status >= 2").fetchone()["c"]
        failed = db.execute("SELECT COUNT(*) AS c FROM papers WHERE parse_status < 0").fetchone()["c"]
        chunks = db.execute("SELECT COUNT(*) AS c FROM paper_chunks").fetchone()["c"]
        venues = db.execute(
            "SELECT COUNT(DISTINCT publish_venue) AS c FROM papers WHERE publish_venue IS NOT NULL AND publish_venue != ''"
        ).fetchone()["c"]
        by_area = [
            dict(row)
            for row in db.execute(
                """
                SELECT COALESCE(research_area, '未分类') AS name, COUNT(*) AS value
                FROM papers GROUP BY COALESCE(research_area, '未分类') ORDER BY value DESC
                """
            ).fetchall()
        ]
        by_year = [
            dict(row)
            for row in db.execute(
                """
                SELECT publish_year AS year, COUNT(*) AS value
                FROM papers
                WHERE publish_year IS NOT NULL
                GROUP BY publish_year
                ORDER BY publish_year
                """
            ).fetchall()
        ]
    return {
        "papers": total,
        "total_papers": total,
        "total_venues": venues,
        "parsed_papers": parsed,
        "failed_papers": failed,
        "exception_papers": failed,
        "vector_chunks": chunks,
        "active_users": 1,
        "area_distribution": by_area,
        "year_distribution": by_year,
    }


def create_paper(item: dict[str, Any], user_id: str = "system", ip: str | None = None) -> dict[str, Any]:
    result = ingest_papers([item], user_id=user_id, ip=ip)
    paper_id = str(item.get("id") or "")
    paper = get_paper(paper_id) if paper_id else None
    if paper is not None:
        with get_db() as db:
            record_audit_log(
                db,
                operate_user_id=user_id,
                user_ip=ip,
                operate_type="PAPER_INGEST",
                operate_sub_type="PAPER_CREATE",
                target_resource_type="paper",
                target_resource_id=paper_id,
                resource_title=paper.get("title"),
                operate_content={"title": paper.get("title"), "source": paper.get("source")},
                is_system_op=user_id == "system",
            )
    return {"result": result, "paper": paper}


def update_paper(
    paper_id: str,
    data: dict[str, Any],
    user_id: str = "system",
    ip: str | None = None,
) -> dict[str, Any]:
    allowed = {
        "publish_venue",
        "title",
        "publish_year",
        "abstract",
        "pdf_url",
        "github_url",
        "arxiv_url",
        "project_url",
        "source_url",
        "arxiv_id",
        "citations",
        "research_area",
        "subfield",
        "task_name",
        "paper_url",
        "source_page",
        "citation_count",
        "minio_pdf_key",
        "parse_status",
        "metadata_updated_at",
        "source_file",
        "raw_metadata",
    }
    values: dict[str, Any] = {key: data[key] for key in allowed if key in data}
    if "author" in data:
        values["author"] = dumps(as_list(data["author"]))
    if "authors" in data:
        values["author"] = dumps(as_list(data["authors"]))
    if "keywords" in data:
        values["keywords"] = dumps(as_list(data["keywords"]))
    if not values:
        paper = get_paper(paper_id)
        if paper is None:
            raise ValueError("paper not found")
        return paper
    values["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in values)
    with get_db() as db:
        cur = db.execute(
            f"UPDATE papers SET {assignments} WHERE id = ?",
            [*values.values(), paper_id],
        )
        if cur.rowcount == 0:
            raise ValueError("paper not found")
        row = db.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        _upsert_search_index(db, dict(row))
        index_paper_elasticsearch(dict(row))
        record_audit_log(
            db,
            operate_user_id=user_id,
            user_ip=ip,
            operate_type="METADATA_CHANGE",
            operate_sub_type="METADATA_UPDATE",
            target_resource_type="paper",
            target_resource_id=paper_id,
            resource_title=dict(row).get("title"),
            operate_content={"changed_fields": sorted(values), "after": values},
            is_system_op=False,
        )
    return get_paper(paper_id)


def delete_paper(paper_id: str, user_id: str = "system", ip: str | None = None) -> dict[str, Any]:
    with get_db() as db:
        row = db.execute("SELECT id, title FROM papers WHERE id = ?", (paper_id,)).fetchone()
        cur = db.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
        record_audit_log(
            db,
            operate_user_id=user_id,
            user_ip=ip,
            operate_type="METADATA_CHANGE",
            operate_sub_type="ARCHIVE_DELETE",
            target_resource_type="paper",
            target_resource_id=paper_id,
            resource_title=row["title"] if row else paper_id,
            operate_content={"deleted": cur.rowcount, "delete_time": utc_now()},
            is_system_op=False,
        )
    delete_paper_elasticsearch(paper_id)
    return {"deleted": cur.rowcount}


def list_exceptions() -> dict[str, Any]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT id AS paper_id, id, title, updated_at AS error_time
            FROM papers WHERE parse_status < 0 ORDER BY updated_at DESC
            """
        ).fetchall()
    items = [
        {**dict(row), "error_type": "PDF_PARSE_FAILED", "retry_count": 0}
        for row in rows
    ]
    return {"total": len(items), "list": items}


def retry_parse(paper_id: str, user_id: str = "system", ip: str | None = None) -> dict[str, Any]:
    with get_db() as db:
        cur = db.execute("UPDATE papers SET parse_status = 1, updated_at = ? WHERE id = ?", (utc_now(), paper_id))
        record_audit_log(
            db,
            operate_user_id=user_id,
            user_ip=ip,
            operate_type="SYSTEM_PERMISSION",
            operate_sub_type="EXCEPTION_RETRY",
            target_resource_type="paper",
            target_resource_id=paper_id,
            resource_title=paper_id,
            operate_content={"parse_status": 1, "retry_time": utc_now()},
            is_system_op=False,
        )
    return {"updated": cur.rowcount, "parse_status": 1}


def list_audit_logs(action: str | None = None) -> dict[str, Any]:
    return query_audit_logs(action=action, page=1, size=200)


def serialize_paper(row: dict[str, Any]) -> dict[str, Any]:
    row["keywords"] = loads_list(row.get("keywords"))
    row["related_papers"] = loads_list(row.get("related_papers"))
    row["tasks"] = loads_list(row.get("tasks"))
    row["methods"] = loads_list(row.get("methods"))
    row["author"] = loads_list(row.get("author"))
    row["authors"] = row["author"]
    row["year"] = row.get("publish_year")
    row["venue"] = row.get("publish_venue")
    return row


def _upsert_search_index(db, paper: dict[str, Any]) -> None:
    searchable = " ".join(
        str(part or "")
        for part in [
            paper["title"],
            paper["abstract"],
            " ".join(loads_list(paper["author"])),
            " ".join(loads_list(paper["keywords"])),
            " ".join(loads_list(paper.get("tasks"))),
            " ".join(loads_list(paper.get("methods"))),
            paper.get("publish_venue"),
            paper.get("research_area"),
            paper.get("subfield"),
        ]
    )
    db.execute(
        """
        INSERT INTO search_index(paper_id, searchable_text, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(paper_id) DO UPDATE SET
            searchable_text = excluded.searchable_text,
            updated_at = excluded.updated_at
        """,
        (paper["id"], searchable, utc_now()),
    )


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_parse_status(item: dict[str, Any]) -> int:
    requested = _int_or_none(item.get("parse_status"))
    if requested is not None:
        return requested
    has_pdf = bool(str(item.get("pdf_url") or item.get("minio_pdf_key") or "").strip())
    return 1 if has_pdf else 0
