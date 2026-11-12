from __future__ import annotations

import hashlib
import json
import re
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

from flask import Blueprint, current_app, g, request, send_file
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from pypdf import PdfReader

from ..extensions import db
from ..models import PersonalKnowledgeFolder, PersonalKnowledgePaper
from .responses import error, ok


bp = Blueprint("academic_space", __name__)
FOLDER_COLORS = {"#47745b", "#356b8c", "#7a6340", "#76558d", "#8a4d52", "#4f6b70"}
ARXIV_ID_PATTERN = re.compile(r"(?<!\d)(\d{4}\.\d{4,5})(?:v\d+)?(?!\d)", re.IGNORECASE)
DOI_PATTERN = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.IGNORECASE)


@bp.get("/academic-space/folders")
def list_folders():
    folders = db.session.scalars(
        select(PersonalKnowledgeFolder)
        .where(
            PersonalKnowledgeFolder.owner_user_id == g.current_user.id,
            PersonalKnowledgeFolder.status == "ACTIVE",
        )
        .order_by(PersonalKnowledgeFolder.created_at)
    ).all()
    counts = dict(
        db.session.execute(
            select(PersonalKnowledgePaper.folder_id, func.count(PersonalKnowledgePaper.id))
            .where(
                PersonalKnowledgePaper.owner_user_id == g.current_user.id,
                PersonalKnowledgePaper.status == "ACTIVE",
            )
            .group_by(PersonalKnowledgePaper.folder_id)
        ).all()
    )
    return ok([serialize_folder(item, counts.get(item.id, 0)) for item in folders])


@bp.post("/academic-space/folders")
def create_folder():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    if not name:
        return error("请输入知识库名称", code="PERSONAL_KB_FOLDER_NAME_REQUIRED")
    if len(name) > 120:
        return error("知识库名称不能超过 120 个字符", code="PERSONAL_KB_FOLDER_NAME_TOO_LONG")
    parent_id = parse_uuid(payload.get("parent_id"))
    if payload.get("parent_id") and parent_id is None:
        return error("父文件夹参数无效", code="PERSONAL_KB_PARENT_INVALID")
    if parent_id and owned_folder(parent_id) is None:
        return error("父文件夹不存在", code="PERSONAL_KB_PARENT_NOT_FOUND", status=404)
    color = str(payload.get("color") or "#47745b")
    if color not in FOLDER_COLORS:
        color = "#47745b"
    folder = PersonalKnowledgeFolder(
        owner_user_id=g.current_user.id,
        parent_folder_id=parent_id,
        name=name,
        description=str(payload.get("description") or "").strip()[:1000] or None,
        color=color,
    )
    db.session.add(folder)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return error("已存在同名知识库", code="PERSONAL_KB_FOLDER_DUPLICATE", status=409)
    return ok(serialize_folder(folder, 0), status=201)


@bp.patch("/academic-space/folders/<uuid:folder_id>")
def update_folder(folder_id: UUID):
    folder = owned_folder(folder_id)
    if folder is None:
        return folder_not_found()
    payload = request.get_json(silent=True) or {}
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            return error("知识库名称不能为空", code="PERSONAL_KB_FOLDER_NAME_REQUIRED")
        folder.name = name[:120]
    if "description" in payload:
        folder.description = str(payload.get("description") or "").strip()[:1000] or None
    if str(payload.get("color") or "") in FOLDER_COLORS:
        folder.color = str(payload["color"])
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return error("已存在同名知识库", code="PERSONAL_KB_FOLDER_DUPLICATE", status=409)
    count = db.session.scalar(
        select(func.count(PersonalKnowledgePaper.id)).where(
            PersonalKnowledgePaper.owner_user_id == g.current_user.id,
            PersonalKnowledgePaper.folder_id == folder.id,
            PersonalKnowledgePaper.status == "ACTIVE",
        )
    ) or 0
    return ok(serialize_folder(folder, count))


@bp.delete("/academic-space/folders/<uuid:folder_id>")
def delete_folder(folder_id: UUID):
    folder = owned_folder(folder_id)
    if folder is None:
        return folder_not_found()
    child_count = db.session.scalar(
        select(func.count(PersonalKnowledgeFolder.id)).where(
            PersonalKnowledgeFolder.owner_user_id == g.current_user.id,
            PersonalKnowledgeFolder.parent_folder_id == folder_id,
            PersonalKnowledgeFolder.status == "ACTIVE",
        )
    ) or 0
    paper_count = db.session.scalar(
        select(func.count(PersonalKnowledgePaper.id)).where(
            PersonalKnowledgePaper.owner_user_id == g.current_user.id,
            PersonalKnowledgePaper.folder_id == folder_id,
            PersonalKnowledgePaper.status == "ACTIVE",
        )
    ) or 0
    if child_count or paper_count:
        return error("请先移除该知识库中的文献和子文件夹", code="PERSONAL_KB_FOLDER_NOT_EMPTY", status=409)
    folder.status = "DELETED"
    db.session.commit()
    return ok({"id": str(folder.id), "deleted": True})


@bp.get("/academic-space/papers")
def list_personal_papers():
    folder_id = parse_uuid(request.args.get("folder_id"))
    if request.args.get("folder_id") and (folder_id is None or owned_folder(folder_id) is None):
        return folder_not_found()
    page = max(1, request.args.get("page", 1, type=int))
    size = min(50, max(1, request.args.get("size", 20, type=int)))
    search = str(request.args.get("search") or "").strip()
    conditions = [
        PersonalKnowledgePaper.owner_user_id == g.current_user.id,
        PersonalKnowledgePaper.status == "ACTIVE",
    ]
    if folder_id:
        conditions.append(PersonalKnowledgePaper.folder_id == folder_id)
    if search:
        like = f"%{search}%"
        conditions.append(
            or_(PersonalKnowledgePaper.title.ilike(like), PersonalKnowledgePaper.publish_venue.ilike(like))
        )
    total = db.session.scalar(select(func.count(PersonalKnowledgePaper.id)).where(*conditions)) or 0
    papers = db.session.scalars(
        select(PersonalKnowledgePaper)
        .where(*conditions)
        .order_by(PersonalKnowledgePaper.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return ok(
        [serialize_paper(item) for item in papers],
        meta={"total": total, "page": page, "size": size, "pages": max(1, (total + size - 1) // size)},
    )


@bp.post("/academic-space/papers/upload")
def upload_personal_paper():
    folder_id = parse_uuid(request.form.get("folder_id"))
    folder = owned_folder(folder_id) if folder_id else None
    if folder is None:
        return folder_not_found()
    file = request.files.get("file")
    if file is None or not file.filename:
        return error("请选择 PDF 文献", code="PERSONAL_KB_FILE_REQUIRED")
    if Path(file.filename).suffix.lower() != ".pdf":
        return error("本地文献仅支持 PDF 文件", code="PERSONAL_KB_FILE_TYPE_INVALID", status=415)
    max_bytes = int(current_app.config["PERSONAL_KB_UPLOAD_MAX_BYTES"])
    content = file.read(max_bytes + 1)
    if len(content) > max_bytes:
        return error("PDF 文件超过 50MB 限制", code="PERSONAL_KB_FILE_TOO_LARGE", status=413)
    if not content.startswith(b"%PDF-"):
        return error("文件不是有效的 PDF", code="PERSONAL_KB_FILE_INVALID", status=415)

    identity = inspect_pdf_identity(file.filename, content, request.form.get("title"))
    if not parse_boolean(request.form.get("force_local")):
        platform_match = find_platform_duplicate(identity)
        if platform_match is not None:
            row, reason = platform_match
            return ok(
                {
                    "status": "DUPLICATE_FOUND",
                    "file_name": Path(file.filename).name,
                    "match_reason": reason,
                    "detected": identity,
                    "platform_paper": serialize_platform_paper(row),
                }
            )

        existing_local = db.session.scalar(
            select(PersonalKnowledgePaper).where(
                PersonalKnowledgePaper.owner_user_id == g.current_user.id,
                PersonalKnowledgePaper.status == "ACTIVE",
                PersonalKnowledgePaper.metadata_json["file_sha256"].astext == identity["file_sha256"],
            )
        )
        if existing_local is not None:
            return ok(
                {
                    "status": "DUPLICATE_FOUND",
                    "file_name": Path(file.filename).name,
                    "match_reason": "FILE_SHA256",
                    "detected": identity,
                    "existing_personal_paper": serialize_paper(existing_local),
                }
            )

    paper_id = uuid4()
    relative_path = Path(str(g.current_user.id)) / f"{paper_id}.pdf"
    destination = Path(current_app.config["PERSONAL_KB_UPLOAD_DIR"]) / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    title = str(request.form.get("title") or Path(file.filename).stem).strip()[:1000]
    authors = parse_author_input(request.form.get("authors"))
    paper = PersonalKnowledgePaper(
        id=paper_id,
        owner_user_id=g.current_user.id,
        folder_id=folder.id,
        source_type="LOCAL_UPLOAD",
        title=title or "未命名文献",
        authors=authors,
        publish_venue=str(request.form.get("venue") or "").strip()[:500] or None,
        publish_year=parse_year(request.form.get("year")),
        object_key=relative_path.as_posix(),
        original_file_name=Path(file.filename).name[:500],
        file_size=len(content),
        metadata_json={
            "content_type": "application/pdf",
            "file_sha256": identity["file_sha256"],
            "detected_arxiv_id": identity.get("arxiv_id"),
            "detected_doi": identity.get("doi"),
            "detected_title": identity.get("title"),
        },
    )
    db.session.add(paper)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        destination.unlink(missing_ok=True)
        raise
    return ok(serialize_paper(paper), status=201)


@bp.get("/academic-space/platform-papers")
def search_platform_papers():
    search = str(request.args.get("search") or "").strip()
    page = max(1, request.args.get("page", 1, type=int))
    size = min(30, max(1, request.args.get("size", 15, type=int)))
    where = ["delete_time IS NULL"]
    params: dict[str, object] = {"limit": size, "offset": (page - 1) * size}
    if search:
        where.append(
            "(title ILIKE :search OR author ILIKE :search OR abstract ILIKE :search "
            "OR id ILIKE :search OR arxiv_id ILIKE :search OR pdf_url ILIKE :search "
            "OR arxiv_url ILIKE :search OR source_url ILIKE :search)"
        )
        params["search"] = f"%{search}%"
    where_sql = " AND ".join(where)
    total = db.session.scalar(text(f"SELECT count(*) FROM knowledge_base.papers WHERE {where_sql}"), params) or 0
    rows = db.session.execute(
        text(
            f"""
            SELECT id, title, author, abstract, publish_venue, publish_year,
                   COALESCE(source_url, pdf_url, paper_url, arxiv_url) AS source_url,
                   source, parse_status, citation_count
            FROM knowledge_base.papers
            WHERE {where_sql}
            ORDER BY COALESCE(citation_count, citations, 0) DESC, created_at DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).mappings().all()
    return ok(
        [serialize_platform_paper(row) for row in rows],
        meta={"total": total, "page": page, "size": size, "pages": max(1, (total + size - 1) // size)},
    )


@bp.post("/academic-space/platform-papers/import")
def import_platform_papers():
    payload = request.get_json(silent=True) or {}
    folder_id = parse_uuid(payload.get("folder_id"))
    folder = owned_folder(folder_id) if folder_id else None
    if folder is None:
        return folder_not_found()
    raw_ids = payload.get("paper_ids")
    paper_ids = list(dict.fromkeys(str(item).strip() for item in raw_ids if str(item).strip())) if isinstance(raw_ids, list) else []
    if not paper_ids:
        return error("请选择需要加载的文献", code="PLATFORM_PAPERS_REQUIRED")
    if len(paper_ids) > 50:
        return error("单次最多加载 50 篇文献", code="PLATFORM_PAPERS_LIMIT_EXCEEDED")
    rows = db.session.execute(
        text(
            """
            SELECT id, title, author, abstract, publish_venue, publish_year,
                   COALESCE(source_url, pdf_url, paper_url, arxiv_url) AS source_url,
                   source, parse_status, citation_count
            FROM knowledge_base.papers
            WHERE id = ANY(CAST(:paper_ids AS text[])) AND delete_time IS NULL
            """
        ),
        {"paper_ids": paper_ids},
    ).mappings().all()
    existing = set(
        db.session.scalars(
            select(PersonalKnowledgePaper.platform_paper_id).where(
                PersonalKnowledgePaper.owner_user_id == g.current_user.id,
                PersonalKnowledgePaper.folder_id == folder.id,
                PersonalKnowledgePaper.platform_paper_id.in_(paper_ids),
                PersonalKnowledgePaper.status == "ACTIVE",
            )
        ).all()
    )
    imported: list[PersonalKnowledgePaper] = []
    for row in rows:
        if row["id"] in existing:
            continue
        snapshot = serialize_platform_paper(row)
        item = PersonalKnowledgePaper(
            owner_user_id=g.current_user.id,
            folder_id=folder.id,
            source_type="PLATFORM_REFERENCE",
            platform_paper_id=row["id"],
            title=row["title"] or "未命名文献",
            authors=snapshot["authors"],
            abstract=row["abstract"],
            publish_venue=row["publish_venue"],
            publish_year=row["publish_year"],
            source_url=row["source_url"],
            metadata_json={
                "platform_source": row["source"],
                "parse_status": row["parse_status"],
                "citation_count": row["citation_count"],
            },
        )
        db.session.add(item)
        imported.append(item)
    db.session.commit()
    return ok(
        {"imported": len(imported), "skipped": len(paper_ids) - len(imported), "papers": [serialize_paper(item) for item in imported]},
        status=201,
    )


@bp.patch("/academic-space/papers/<uuid:paper_id>")
def move_personal_paper(paper_id: UUID):
    paper = owned_paper(paper_id)
    if paper is None:
        return paper_not_found()
    payload = request.get_json(silent=True) or {}
    folder_id = parse_uuid(payload.get("folder_id"))
    folder = owned_folder(folder_id) if folder_id else None
    if folder is None:
        return folder_not_found()
    paper.folder_id = folder.id
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return error("目标知识库已包含该平台文献", code="PERSONAL_KB_PAPER_DUPLICATE", status=409)
    return ok(serialize_paper(paper))


@bp.delete("/academic-space/papers/<uuid:paper_id>")
def delete_personal_paper(paper_id: UUID):
    paper = owned_paper(paper_id)
    if paper is None:
        return paper_not_found()
    object_path = local_object_path(paper)
    db.session.delete(paper)
    db.session.commit()
    if object_path:
        object_path.unlink(missing_ok=True)
    return ok({"id": str(paper_id), "deleted": True})


@bp.get("/academic-space/papers/<uuid:paper_id>/file")
def download_personal_paper(paper_id: UUID):
    paper = owned_paper(paper_id)
    if paper is None:
        return paper_not_found()
    if paper.source_type != "LOCAL_UPLOAD":
        return error("平台文献请通过原始来源访问", code="PERSONAL_KB_FILE_NOT_LOCAL", status=409)
    path = local_object_path(paper)
    if path is None or not path.is_file():
        return error("本地文献文件不存在", code="PERSONAL_KB_FILE_NOT_FOUND", status=404)
    return send_file(path, as_attachment=True, download_name=paper.original_file_name or f"{paper.id}.pdf", mimetype="application/pdf")


def owned_folder(folder_id: UUID) -> PersonalKnowledgeFolder | None:
    return db.session.scalar(
        select(PersonalKnowledgeFolder).where(
            PersonalKnowledgeFolder.id == folder_id,
            PersonalKnowledgeFolder.owner_user_id == g.current_user.id,
            PersonalKnowledgeFolder.status == "ACTIVE",
        )
    )


def owned_paper(paper_id: UUID) -> PersonalKnowledgePaper | None:
    return db.session.scalar(
        select(PersonalKnowledgePaper).where(
            PersonalKnowledgePaper.id == paper_id,
            PersonalKnowledgePaper.owner_user_id == g.current_user.id,
            PersonalKnowledgePaper.status == "ACTIVE",
        )
    )


def local_object_path(paper: PersonalKnowledgePaper) -> Path | None:
    if not paper.object_key:
        return None
    root = Path(current_app.config["PERSONAL_KB_UPLOAD_DIR"]).resolve()
    candidate = (root / paper.object_key).resolve()
    return candidate if candidate == root or root in candidate.parents else None


def parse_uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def parse_year(value: object) -> int | None:
    try:
        year = int(str(value))
    except (TypeError, ValueError):
        return None
    return year if 1000 <= year <= 2200 else None


def parse_boolean(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def inspect_pdf_identity(filename: str, content: bytes, supplied_title: object = None) -> dict:
    stem = Path(filename or "paper.pdf").stem.strip()
    arxiv_id = extract_arxiv_id(stem)
    doi = None
    title = str(supplied_title or "").strip() or None
    first_page_text = ""
    try:
        reader = PdfReader(BytesIO(content))
        metadata_title = str((reader.metadata or {}).get("/Title") or "").strip()
        if not title and is_meaningful_pdf_title(metadata_title, stem):
            title = metadata_title
        if reader.pages:
            first_page_text = (reader.pages[0].extract_text() or "")[:16000]
    except Exception:
        pass

    if not arxiv_id:
        labelled = re.search(r"arXiv\s*:\s*(\d{4}\.\d{4,5})(?:v\d+)?", first_page_text, re.IGNORECASE)
        if labelled:
            arxiv_id = labelled.group(1)
    doi_match = DOI_PATTERN.search(first_page_text)
    if doi_match:
        doi = doi_match.group(1).rstrip(".,;)").lower()
    if not title:
        title = extract_title_from_first_page(first_page_text)
    return {
        "arxiv_id": arxiv_id,
        "doi": doi,
        "title": title,
        "normalized_title": normalize_paper_title(title),
        "file_sha256": hashlib.sha256(content).hexdigest(),
    }


def extract_arxiv_id(value: object) -> str | None:
    match = ARXIV_ID_PATTERN.search(str(value or ""))
    return match.group(1) if match else None


def normalize_paper_title(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def is_meaningful_pdf_title(title: str, filename_stem: str) -> bool:
    normalized = normalize_paper_title(title)
    return len(normalized) >= 12 and normalized not in {
        normalize_paper_title(filename_stem), "untitled", "microsoftworddocument"
    }


def extract_title_from_first_page(text_value: str) -> str | None:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text_value.splitlines()]
    candidates = []
    for line in lines[:30]:
        if re.search(r"^(arxiv|doi|preprint|submitted|https?://)", line, re.IGNORECASE):
            continue
        if 20 <= len(line) <= 280 and not line.lower().startswith("abstract"):
            candidates.append(line)
        if line.lower().startswith("abstract"):
            break
    return candidates[0] if candidates else None


def find_platform_duplicate(identity: dict):
    arxiv_id = identity.get("arxiv_id")
    doi = identity.get("doi")
    normalized_title = identity.get("normalized_title")
    conditions = []
    params: dict[str, object] = {}
    if arxiv_id:
        conditions.append(
            "(id = :arxiv_id OR arxiv_id = :arxiv_id OR COALESCE(pdf_url, '') ILIKE :arxiv_like "
            "OR COALESCE(arxiv_url, '') ILIKE :arxiv_like OR COALESCE(source_url, '') ILIKE :arxiv_like)"
        )
        params.update(arxiv_id=arxiv_id, arxiv_like=f"%{arxiv_id}%")
    if doi:
        conditions.append(
            "(COALESCE(raw_metadata, '') ILIKE :doi_like OR COALESCE(source_url, '') ILIKE :doi_like "
            "OR COALESCE(paper_url, '') ILIKE :doi_like)"
        )
        params["doi_like"] = f"%{doi}%"
    if normalized_title and len(normalized_title) >= 12:
        conditions.append("regexp_replace(lower(title), '[^a-z0-9]+', '', 'g') = :normalized_title")
        params["normalized_title"] = normalized_title
    if not conditions:
        return None

    row = db.session.execute(
        text(
            f"""
            SELECT id, title, author, abstract, publish_venue, publish_year,
                   COALESCE(source_url, pdf_url, paper_url, arxiv_url) AS source_url,
                   source, parse_status, citation_count, arxiv_id, pdf_url, arxiv_url,
                   raw_metadata, paper_url
            FROM knowledge_base.papers
            WHERE delete_time IS NULL AND ({' OR '.join(conditions)})
            ORDER BY CASE WHEN id = :preferred_id THEN 0 ELSE 1 END, created_at DESC
            LIMIT 1
            """
        ),
        {**params, "preferred_id": arxiv_id or ""},
    ).mappings().first()
    if row is None:
        return None
    if arxiv_id and (
        row["id"] == arxiv_id
        or row["arxiv_id"] == arxiv_id
        or any(arxiv_id in str(row[key] or "") for key in ("pdf_url", "arxiv_url", "source_url"))
    ):
        reason = "ARXIV_ID"
    elif doi and any(doi in str(row[key] or "").lower() for key in ("raw_metadata", "paper_url", "source_url")):
        reason = "DOI"
    else:
        reason = "TITLE"
    return row, reason


def parse_author_input(value: object) -> list[str]:
    return [item.strip() for item in str(value or "").replace("；", ";").split(";") if item.strip()][:50]


def parse_platform_authors(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    text_value = str(value or "").strip()
    if not text_value:
        return []
    try:
        decoded = json.loads(text_value)
        if isinstance(decoded, list):
            return [str(item) for item in decoded if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return parse_author_input(text_value)


def serialize_folder(folder: PersonalKnowledgeFolder, paper_count: int) -> dict:
    return {
        "id": str(folder.id),
        "parent_id": str(folder.parent_folder_id) if folder.parent_folder_id else None,
        "name": folder.name,
        "description": folder.description or "",
        "color": folder.color,
        "paper_count": int(paper_count),
        "created_at": folder.created_at.isoformat() if folder.created_at else None,
        "updated_at": folder.updated_at.isoformat() if folder.updated_at else None,
    }


def serialize_paper(paper: PersonalKnowledgePaper) -> dict:
    return {
        "id": str(paper.id),
        "folder_id": str(paper.folder_id),
        "source_type": paper.source_type,
        "platform_paper_id": paper.platform_paper_id,
        "title": paper.title,
        "authors": paper.authors or [],
        "abstract": paper.abstract or "",
        "publish_venue": paper.publish_venue or "",
        "publish_year": paper.publish_year,
        "source_url": paper.source_url,
        "original_file_name": paper.original_file_name,
        "file_size": paper.file_size,
        "metadata": paper.metadata_json or {},
        "created_at": paper.created_at.isoformat() if paper.created_at else None,
    }


def serialize_platform_paper(row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"] or "未命名文献",
        "authors": parse_platform_authors(row["author"]),
        "abstract": row["abstract"] or "",
        "publish_venue": row["publish_venue"] or "",
        "publish_year": row["publish_year"],
        "source_url": row["source_url"],
        "source": row["source"] or "platform",
        "parse_status": row["parse_status"],
        "citation_count": row["citation_count"] or 0,
    }


def folder_not_found():
    return error("知识库不存在或无权访问", code="PERSONAL_KB_FOLDER_NOT_FOUND", status=404)


def paper_not_found():
    return error("文献不存在或无权访问", code="PERSONAL_KB_PAPER_NOT_FOUND", status=404)
