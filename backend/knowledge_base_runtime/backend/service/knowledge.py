from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from knowledge_base_runtime.backend.config.settings import PDF_DIR
from knowledge_base_runtime.backend.dao.database import get_db
from knowledge_base_runtime.backend.service.audit import record_audit_log
from knowledge_base_runtime.backend.service.chunking import extract_fulltext_from_pdf_bytes, replace_chunks, upsert_paper_fulltext
from knowledge_base_runtime.backend.service.metadata import get_paper, serialize_paper


CCF_LEVELS = {
    "A": {
        "AAAI", "ACL", "ACM MM", "ASPLOS", "CCS", "CVPR", "FSE", "ICCV",
        "ICML", "ICSE", "IJCAI", "KDD", "MICRO", "NEURIPS", "NIPS", "OSDI",
        "SIGIR", "SIGMOD", "SOSP", "USENIX SECURITY", "VLDB", "WWW",
    },
    "B": {
        "ACCV", "COLING", "ECCV", "EMNLP", "ICDE", "ICDM", "ICME", "ICPR",
        "NAACL", "PAKDD", "PODS", "WACV",
    },
    "C": {"BMVC", "CIKM", "ECIR", "IJCNN", "PRCV", "SDM"},
}

DOMAIN_TERMS = {
    "Vision": ("vision", "visual", "image", "detection", "segmentation", "3d", "computer vision"),
    "Video": ("video", "temporal", "action recognition", "activity recognition"),
    "Language": ("language", "nlp", "text", "linguistic", "translation", "large language", "llm"),
    "Audio": ("audio", "speech", "voice", "acoustic", "music"),
}


def list_knowledge_papers(
    *,
    domain: str = "General",
    search: str | None = None,
    ccf_level: str | None = None,
    sliced: str | None = None,
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    page = max(page, 1)
    size = min(max(size, 1), 100)
    offset = (page - 1) * size
    where = ["1=1"]
    params: list[Any] = []

    if search:
        like = f"%{search.strip()}%"
        where.append("(p.id LIKE ? OR p.title LIKE ? OR p.publish_venue LIKE ?)")
        params.extend([like, like, like])

    _append_domain_filter(where, params, domain)

    level = str(ccf_level or "").upper()
    if level in CCF_LEVELS:
        venues = sorted(CCF_LEVELS[level])
        where.append(f"UPPER(COALESCE(p.publish_venue, '')) IN ({', '.join('?' for _ in venues)})")
        params.extend(venues)

    if sliced == "1":
        where.append("EXISTS (SELECT 1 FROM paper_chunks pc WHERE pc.paper_id = p.id)")
    elif sliced == "0":
        where.append("NOT EXISTS (SELECT 1 FROM paper_chunks pc WHERE pc.paper_id = p.id)")

    where_sql = " AND ".join(where)
    with get_db() as db:
        total = db.execute(f"SELECT COUNT(*) AS c FROM papers p WHERE {where_sql}", params).fetchone()["c"]
        rows = db.execute(
            f"""
            SELECT p.*,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM paper_chunks pc WHERE pc.paper_id = p.id
                   ) THEN 1 ELSE 0 END AS is_sliced
            FROM papers p
            WHERE {where_sql}
            ORDER BY p.updated_at DESC, p.id
            LIMIT ? OFFSET ?
            """,
            [*params, size, offset],
        ).fetchall()

    items = []
    for row in rows:
        paper = serialize_paper(dict(row))
        paper.update(
            {
                "short_name": paper.get("publish_venue") or paper["id"],
                "full_name": paper.get("title") or paper["id"],
                "ccf_level": _ccf_level_for(paper.get("publish_venue")),
                "type": "论文",
                "website": paper.get("paper_url") or paper.get("pdf_url") or "",
                "is_sliced": bool(paper.get("is_sliced")),
            }
        )
        items.append(paper)
    return {"total": total, "page": page, "size": size, "list": items}


def slice_papers(payload: dict[str, Any], user_id: str, ip: str | None = None) -> dict[str, Any]:
    method = str(payload.get("method") or "fixed_1024_200")
    strategy = str(payload.get("strategy") or payload.get("method") or "fixed_1024_200")
    if method != "fixed_1024_200" and strategy not in {"fixed_boundary_v1", "paragraph_sentence_v1", "section_parent_child_v1", "semantic_bge_m3_v1"}:
        raise ValueError("unsupported strategy")

    raw_ids = payload.get("paper_ids") or []
    if not isinstance(raw_ids, list):
        raise ValueError("paper_ids must be an array")
    paper_ids = list(dict.fromkeys(str(value).strip() for value in raw_ids if str(value).strip()))
    if not paper_ids:
        raise ValueError("paper_ids is required")
    if len(paper_ids) > 100:
        raise ValueError("a maximum of 100 papers can be sliced at once")

    completed: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for paper_id in paper_ids:
        paper = get_paper(paper_id)
        if paper is None:
            failed.append({"paper_id": paper_id, "error": "paper not found"})
            continue
        try:
            text = _paper_text(paper)
            chunk_count, warnings = replace_chunks(paper_id, text, strategy=strategy)
            if chunk_count == 0:
                raise ValueError("paper has no text available for slicing")
            completed.append({"paper_id": paper_id, "chunk_count": chunk_count, "warnings": warnings})
        except Exception as exc:
            failed.append({"paper_id": paper_id, "error": str(exc)})

    task_id = f"slice_{uuid.uuid4().hex[:12]}"
    with get_db() as db:
        record_audit_log(
            db,
            operate_user_id=user_id,
            user_ip=ip,
            operate_type="CHUNK",
            operate_sub_type="MANUAL_CHUNK",
            target_resource_type="paper_batch",
            target_resource_id=task_id,
            resource_title=f"切片任务 {task_id}",
            operate_content={
                "method": method,
                "strategy": strategy,
                "paper_count": len(paper_ids),
                "completed_count": len(completed),
                "failed_count": len(failed),
                "completed": completed,
                "failed": failed,
            },
            is_system_op=False,
        )
    return {
        "task_id": task_id,
        "status": "completed" if completed and not failed else "partial" if completed else "failed",
        "completed": completed,
        "failed": failed,
    }


def list_paper_chunks(paper_id: str, page: int = 1, size: int = 20) -> dict[str, Any]:
    page = max(page, 1)
    size = min(max(size, 1), 100)
    offset = (page - 1) * size
    with get_db() as db:
        paper = db.execute(
            "SELECT id, title, publish_venue, publish_year FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()
        if paper is None:
            raise ValueError("paper not found")
        total = db.execute(
            "SELECT COUNT(*) AS c FROM paper_chunks WHERE paper_id = ?",
            (paper_id,),
        ).fetchone()["c"]
        rows = db.execute(
            """
            SELECT chunk_id, paper_id, chunk_index, content, page_no, vector_key, section_path, parent_chunk_id, created_at
            FROM paper_chunks
            WHERE paper_id = ?
            ORDER BY chunk_index
            LIMIT ? OFFSET ?
            """,
            (paper_id, size, offset),
        ).fetchall()

    chunks = []
    for row in rows:
        item = dict(row)
        try:
            item["section_path"] = json.loads(item.get("section_path") or "[]")
        except (TypeError, ValueError):
            item["section_path"] = []
        item["content_length"] = len(item.get("content") or "")
        chunks.append(item)
    return {
        "paper": dict(paper),
        "total": total,
        "page": page,
        "size": size,
        "list": chunks,
    }


def _paper_text(paper: dict[str, Any]) -> str:
    with get_db() as db:
        row = db.execute(
            "SELECT clean_text FROM paper_fulltexts WHERE paper_id = ?",
            (paper.get("id"),),
        ).fetchone()
    if row and row["clean_text"]:
        return str(row["clean_text"])

    pdf_key = str(paper.get("minio_pdf_key") or "")
    if pdf_key:
        pdf_path = PDF_DIR / Path(pdf_key).name
        if pdf_path.exists():
            extraction = extract_fulltext_from_pdf_bytes(pdf_path.read_bytes())
            if extraction.clean_text:
                upsert_paper_fulltext(
                    str(paper.get("id") or ""),
                    minio_pdf_key=pdf_key,
                    raw_text=extraction.raw_text,
                    clean_text=extraction.clean_text,
                    extraction_method=extraction.extraction_method,
                    clean_strategy=extraction.clean_strategy,
                    mojibake_hits=extraction.mojibake_hits,
                )
                return extraction.clean_text
    return " ".join(str(part or "").strip() for part in [paper.get("title"), paper.get("abstract")]).strip()


def _ccf_level_for(venue: Any) -> str:
    normalized = str(venue or "").strip().upper()
    for level, venues in CCF_LEVELS.items():
        if normalized in venues:
            return level
    return ""


def _append_domain_filter(where: list[str], params: list[Any], domain: str) -> None:
    normalized = str(domain or "General").title()
    if normalized == "General":
        return
    searchable = "LOWER(COALESCE(p.research_area, '') || ' ' || COALESCE(p.subfield, '') || ' ' || COALESCE(p.title, ''))"
    if normalized in DOMAIN_TERMS:
        terms = DOMAIN_TERMS[normalized]
        where.append("(" + " OR ".join(f"{searchable} LIKE ?" for _ in terms) + ")")
        params.extend(f"%{term}%" for term in terms)
        return
    if normalized == "Other":
        terms = tuple(term for values in DOMAIN_TERMS.values() for term in values)
        where.append("(" + " AND ".join(f"{searchable} NOT LIKE ?" for _ in terms) + ")")
        params.extend(f"%{term}%" for term in terms)
