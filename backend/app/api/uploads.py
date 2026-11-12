from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import BadZipFile, ZipFile

from flask import Blueprint, current_app, g, request
from PIL import Image

from .responses import error, ok


bp = Blueprint("uploads", __name__)
MANUSCRIPT_SUFFIXES = {".md", ".txt", ".docx", ".pdf"}
PATENT_SUFFIXES = {
    ".md",
    ".markdown",
    ".txt",
    ".docx",
    ".pptx",
    ".ppsx",
    ".pdf",
    ".py",
    ".go",
    ".java",
    ".js",
    ".ts",
    ".tsx",
    ".rs",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
}
FIGURE_SUFFIXES = {
    "data": {".csv", ".tsv", ".xlsx", ".xls", ".json", ".jsonl"},
    "context": {".pdf", ".docx", ".txt", ".md", ".tex"},
    "sketch": {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"},
}


@bp.post("/uploads/papers")
def upload_paper():
    file = request.files.get("file")
    if file is None or not file.filename:
        return error("请选择 PDF 论文文件", code="PAPER_FILE_REQUIRED", status=400)
    if not file.filename.lower().endswith(".pdf"):
        return error("论文精读仅支持 PDF 文件", code="PAPER_FILE_TYPE_INVALID", status=415)

    max_bytes = int(current_app.config["PAPER_UPLOAD_MAX_BYTES"])
    content = file.read(max_bytes + 1)
    if len(content) > max_bytes:
        return error("PDF 文件超过大小限制", code="PAPER_FILE_TOO_LARGE", status=413)
    if not content.startswith(b"%PDF-"):
        return error("文件不是有效的 PDF", code="PAPER_FILE_INVALID", status=415)

    upload_id = uuid4()
    user_dir = Path(current_app.config["PAPER_UPLOAD_DIR"]) / str(g.current_user.id)
    user_dir.mkdir(parents=True, exist_ok=True)
    destination = user_dir / f"{upload_id}.pdf"
    destination.write_bytes(content)
    return ok(
        {
            "uploadId": str(upload_id),
            "fileName": Path(file.filename).name,
            "size": len(content),
        },
        status=201,
    )


def resolve_paper_upload(user_id: UUID, upload_id: object) -> Path | None:
    try:
        normalized_id = UUID(str(upload_id))
    except (ValueError, TypeError, AttributeError):
        return None
    path = Path(current_app.config["PAPER_UPLOAD_DIR"]) / str(user_id) / f"{normalized_id}.pdf"
    return path if path.is_file() else None


@bp.post("/uploads/manuscripts")
def upload_manuscript():
    file = request.files.get("file")
    if file is None or not file.filename:
        return error("请选择待检测稿件", code="MANUSCRIPT_FILE_REQUIRED", status=400)
    suffix = Path(file.filename).suffix.lower()
    if suffix not in MANUSCRIPT_SUFFIXES:
        return error(
            "学术合规检测仅支持 MD、TXT、DOCX 和 PDF 文件",
            code="MANUSCRIPT_FILE_TYPE_INVALID",
            status=415,
        )

    max_bytes = int(current_app.config["COMPLIANCE_UPLOAD_MAX_BYTES"])
    content = file.read(max_bytes + 1)
    if len(content) > max_bytes:
        return error("稿件超过大小限制", code="MANUSCRIPT_FILE_TOO_LARGE", status=413)
    if not _valid_manuscript_content(content, suffix):
        return error("稿件内容与文件格式不匹配或文件已损坏", code="MANUSCRIPT_FILE_INVALID", status=415)

    upload_id = uuid4()
    user_dir = Path(current_app.config["COMPLIANCE_UPLOAD_DIR"]) / str(g.current_user.id)
    user_dir.mkdir(parents=True, exist_ok=True)
    destination = user_dir / f"{upload_id}{suffix}"
    destination.write_bytes(content)
    return ok(
        {
            "uploadId": str(upload_id),
            "fileName": Path(file.filename).name,
            "size": len(content),
            "fileType": suffix.removeprefix("."),
        },
        status=201,
    )


def resolve_manuscript_upload(user_id: UUID, upload_id: object) -> Path | None:
    try:
        normalized_id = UUID(str(upload_id))
    except (ValueError, TypeError, AttributeError):
        return None
    user_dir = Path(current_app.config["COMPLIANCE_UPLOAD_DIR"]) / str(user_id)
    matches = [user_dir / f"{normalized_id}{suffix}" for suffix in MANUSCRIPT_SUFFIXES]
    existing = [path for path in matches if path.is_file()]
    return existing[0] if len(existing) == 1 else None


@bp.post("/uploads/translations")
def upload_translation_document():
    file = request.files.get("file")
    if file is None or not file.filename:
        return error("请选择待翻译的学术文档", code="TRANSLATION_FILE_REQUIRED", status=400)
    suffix = Path(file.filename).suffix.lower()
    if suffix not in MANUSCRIPT_SUFFIXES:
        return error(
            "学术翻译仅支持 MD、TXT、DOCX 和 PDF 文件",
            code="TRANSLATION_FILE_TYPE_INVALID",
            status=415,
        )
    max_bytes = int(current_app.config["TRANSLATION_UPLOAD_MAX_BYTES"])
    content = file.read(max_bytes + 1)
    if len(content) > max_bytes:
        return error("待翻译文档超过大小限制", code="TRANSLATION_FILE_TOO_LARGE", status=413)
    if not _valid_manuscript_content(content, suffix):
        return error(
            "文档内容与文件格式不匹配或文件已损坏",
            code="TRANSLATION_FILE_INVALID",
            status=415,
        )

    upload_id = uuid4()
    user_dir = Path(current_app.config["TRANSLATION_UPLOAD_DIR"]) / str(g.current_user.id)
    user_dir.mkdir(parents=True, exist_ok=True)
    destination = user_dir / f"{upload_id}{suffix}"
    destination.write_bytes(content)
    return ok(
        {
            "uploadId": str(upload_id),
            "fileName": Path(file.filename).name,
            "size": len(content),
            "fileType": suffix.removeprefix("."),
        },
        status=201,
    )


def resolve_translation_upload(user_id: UUID, upload_id: object) -> Path | None:
    try:
        normalized_id = UUID(str(upload_id))
    except (ValueError, TypeError, AttributeError):
        return None
    user_dir = Path(current_app.config["TRANSLATION_UPLOAD_DIR"]) / str(user_id)
    existing = [
        user_dir / f"{normalized_id}{suffix}"
        for suffix in MANUSCRIPT_SUFFIXES
        if (user_dir / f"{normalized_id}{suffix}").is_file()
    ]
    return existing[0] if len(existing) == 1 else None


@bp.post("/uploads/patents")
def upload_patent_material():
    file = request.files.get("file")
    if file is None or not file.filename:
        return error("请选择专利技术材料", code="PATENT_FILE_REQUIRED", status=400)
    suffix = Path(file.filename).suffix.lower()
    if suffix not in PATENT_SUFFIXES:
        return error(
            "专利材料格式不受支持",
            code="PATENT_FILE_TYPE_INVALID",
            status=415,
        )
    max_bytes = int(current_app.config["PATENT_UPLOAD_MAX_BYTES"])
    content = file.read(max_bytes + 1)
    if len(content) > max_bytes:
        return error("专利技术材料超过大小限制", code="PATENT_FILE_TOO_LARGE", status=413)
    if not _valid_patent_content(content, suffix):
        return error(
            "技术材料内容与文件格式不匹配或文件已损坏",
            code="PATENT_FILE_INVALID",
            status=415,
        )
    upload_id = uuid4()
    user_dir = Path(current_app.config["PATENT_UPLOAD_DIR"]) / str(g.current_user.id)
    user_dir.mkdir(parents=True, exist_ok=True)
    destination = user_dir / f"{upload_id}{suffix}"
    destination.write_bytes(content)
    return ok(
        {
            "uploadId": str(upload_id),
            "fileName": Path(file.filename).name,
            "size": len(content),
            "fileType": suffix.removeprefix("."),
        },
        status=201,
    )


def resolve_patent_upload(user_id: UUID, upload_id: object) -> Path | None:
    try:
        normalized_id = UUID(str(upload_id))
    except (ValueError, TypeError, AttributeError):
        return None
    user_dir = Path(current_app.config["PATENT_UPLOAD_DIR"]) / str(user_id)
    existing = [
        user_dir / f"{normalized_id}{suffix}"
        for suffix in PATENT_SUFFIXES
        if (user_dir / f"{normalized_id}{suffix}").is_file()
    ]
    return existing[0] if len(existing) == 1 else None


@bp.post("/uploads/figures")
def upload_figure_input():
    file = request.files.get("file")
    kind = str(request.form.get("kind") or "").strip().lower()
    if kind not in FIGURE_SUFFIXES:
        return error("绘图输入类型无效", code="FIGURE_INPUT_KIND_INVALID", status=400)
    if file is None or not file.filename:
        return error("请选择绘图输入文件", code="FIGURE_FILE_REQUIRED", status=400)
    suffix = Path(file.filename).suffix.lower()
    if suffix not in FIGURE_SUFFIXES[kind]:
        return error("该文件格式不适用于当前输入类型", code="FIGURE_FILE_TYPE_INVALID", status=415)
    max_bytes = int(current_app.config["FIGURE_UPLOAD_MAX_BYTES"])
    content = file.read(max_bytes + 1)
    if len(content) > max_bytes:
        return error("绘图输入文件超过大小限制", code="FIGURE_FILE_TOO_LARGE", status=413)
    if not _valid_figure_content(content, suffix, kind):
        return error("绘图输入文件已损坏或内容格式不匹配", code="FIGURE_FILE_INVALID", status=415)
    upload_id = uuid4()
    user_dir = Path(current_app.config["FIGURE_UPLOAD_DIR"]) / str(g.current_user.id) / kind
    user_dir.mkdir(parents=True, exist_ok=True)
    destination = user_dir / f"{upload_id}{suffix}"
    destination.write_bytes(content)
    return ok(
        {
            "uploadId": str(upload_id),
            "fileName": Path(file.filename).name,
            "size": len(content),
            "fileType": suffix.removeprefix("."),
            "kind": kind,
        },
        status=201,
    )


def resolve_figure_upload(user_id: UUID, upload_id: object, kind: str) -> Path | None:
    if kind not in FIGURE_SUFFIXES:
        return None
    try:
        normalized_id = UUID(str(upload_id))
    except (ValueError, TypeError, AttributeError):
        return None
    user_dir = Path(current_app.config["FIGURE_UPLOAD_DIR"]) / str(user_id) / kind
    existing = [
        user_dir / f"{normalized_id}{suffix}"
        for suffix in FIGURE_SUFFIXES[kind]
        if (user_dir / f"{normalized_id}{suffix}").is_file()
    ]
    return existing[0] if len(existing) == 1 else None


def _valid_manuscript_content(content: bytes, suffix: str) -> bool:
    if not content:
        return False
    if suffix == ".pdf":
        return content.startswith(b"%PDF-")
    if suffix == ".docx":
        try:
            with ZipFile(BytesIO(content)) as archive:
                return "word/document.xml" in archive.namelist()
        except BadZipFile:
            return False
    try:
        content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    return True


def _valid_patent_content(content: bytes, suffix: str) -> bool:
    if suffix in MANUSCRIPT_SUFFIXES:
        return _valid_manuscript_content(content, suffix)
    if not content:
        return False
    if suffix in {".pptx", ".ppsx"}:
        try:
            with ZipFile(BytesIO(content)) as archive:
                names = set(archive.namelist())
                return "[Content_Types].xml" in names and any(
                    name.startswith("ppt/slides/slide") for name in names
                )
        except BadZipFile:
            return False
    try:
        content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    return True


def _valid_figure_content(content: bytes, suffix: str, kind: str) -> bool:
    if not content:
        return False
    if kind == "sketch":
        try:
            with Image.open(BytesIO(content)) as image:
                image.verify()
            return True
        except Exception:  # noqa: BLE001
            return False
    if suffix == ".pdf":
        return content.startswith(b"%PDF-")
    if suffix == ".docx":
        return _valid_manuscript_content(content, suffix)
    if suffix == ".xlsx":
        try:
            with ZipFile(BytesIO(content)) as archive:
                return "xl/workbook.xml" in archive.namelist()
        except BadZipFile:
            return False
    if suffix == ".xls":
        return content.startswith(bytes.fromhex("D0CF11E0A1B11AE1"))
    try:
        text = content.decode("utf-8-sig")
        if suffix == ".json":
            json.loads(text)
        elif suffix == ".jsonl":
            for line in text.splitlines():
                if line.strip():
                    json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return True
