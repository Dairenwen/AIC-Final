"""使用标准库生成 FINALIZE 第一版 Markdown、Word 与 Excel 文件。

来源：backend/app/graphs/finalize_export.py

本模块全部为无 DB 纯函数/文件生成逻辑。
DB 快照组装、GraphRun 状态机等仍由 B4 finalize 图负责。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape


# 包根：langgraph-agent/（src/langgraph_agent/tools → parents[3]）
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
_EXPORT_ROOT = _PACKAGE_ROOT / ".tmp" / "finalize_exports"

# 对外可下载的三种格式：规范名 → (下载文件名, Content-Type)
_EXPORT_FORMAT_META: dict[str, tuple[str, str]] = {
    "MARKDOWN": ("review-response.md", "text/markdown; charset=utf-8"),
    "WORD": (
        "review-response.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "EXCEL": (
        "revision-checklist.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
}
_EXPORT_FORMAT_ALIASES: dict[str, str] = {
    "MARKDOWN": "MARKDOWN",
    "MD": "MARKDOWN",
    "WORD": "WORD",
    "DOCX": "WORD",
    "EXCEL": "EXCEL",
    "XLSX": "EXCEL",
}


def normalize_export_format(raw: str | None) -> str | None:
    """将路径参数规范为 MARKDOWN/WORD/EXCEL；无法识别则返回 None。"""
    if raw is None:
        return None
    token = str(raw).strip().upper()
    if not token:
        return None
    return _EXPORT_FORMAT_ALIASES.get(token)


def export_download_meta(format_name: str) -> tuple[str, str]:
    """返回 (download_name, content_type)；format 必须已是规范名。"""
    try:
        return _EXPORT_FORMAT_META[format_name]
    except KeyError as error:
        raise ValueError(f"不支持的导出格式：{format_name}") from error


def resolve_registered_export_path(storage_uri: str) -> Path:
    """仅允许解析导出根目录下、由 snapshot 登记的相对路径。

    拒绝绝对路径、空串与任何解析后落在导出根之外的路径（防穿越）。
    storage_uri 相对 langgraph-agent 包根。
    """
    raw = str(storage_uri or "").strip()
    if not raw:
        raise ValueError("storage_uri 为空")
    candidate = Path(raw)
    if candidate.is_absolute():
        raise ValueError("storage_uri 必须是包内相对路径")
    # 禁止仍含 .. 的未解析路径直接拼接后越界
    resolved = (_PACKAGE_ROOT / candidate).resolve()
    export_root = _EXPORT_ROOT.resolve()
    try:
        resolved.relative_to(export_root)
    except ValueError as error:
        raise ValueError("storage_uri 超出导出目录") from error
    return resolved


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        delete=False,
    ) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _atomic_zip(path: Path, entries: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "wb", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for name, content in entries.items():
                archive.writestr(name, content.encode("utf-8"))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _paragraph(text: object) -> str:
    value = escape(str(text or ""))
    return f'<w:p><w:r><w:t xml:space="preserve">{value}</w:t></w:r></w:p>'


def _is_approved_exportable_reply(reply: dict[str, Any]) -> bool:
    """仅导出已批准且正文非空的对外回复。"""
    if str(reply.get("reply_status") or "").upper() != "APPROVED":
        return False
    if str(reply.get("draft_status") or "").upper() != "APPROVED":
        return False
    return bool(str(reply.get("content") or "").strip())


def _party_role_rank(role: object) -> int:
    token = str(role or "").strip().upper()
    if token in {"EDITOR", "ASSOCIATE_EDITOR"}:
        return 0
    if token == "REVIEWER":
        return 1
    return 2


def _party_sort_key(
    party_id: str,
    *,
    role: object,
    display_name: object,
    first_index: int,
) -> tuple[int, str, int, str]:
    return (
        _party_role_rank(role),
        str(display_name or "").casefold(),
        first_index,
        party_id,
    )


def group_external_replies_by_party(
    external_replies: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按审稿人分组：EDITOR 优先，同角色按显示名，组内保持原序并编号。

    返回：
    [
      {
        "party_id": str,
        "party_display_name": str,
        "party_role": str,
        "replies": [ {..., "opinion_no": int}, ... ]
      },
      ...
    ]
    """
    groups: dict[str, dict[str, Any]] = {}
    first_seen: dict[str, int] = {}
    ordered_ids: list[str] = []

    for index, raw in enumerate(external_replies or []):
        if not isinstance(raw, dict):
            continue
        if not _is_approved_exportable_reply(raw):
            # 若上游已过滤，status 可能缺失；此时仅要求 content 非空。
            status_missing = (
                raw.get("reply_status") is None and raw.get("draft_status") is None
            )
            if not (status_missing and str(raw.get("content") or "").strip()):
                continue
        party_id = str(
            raw.get("party_id")
            or raw.get("party_display_name")
            or f"party-{index}"
        )
        if party_id not in groups:
            first_seen[party_id] = index
            ordered_ids.append(party_id)
            groups[party_id] = {
                "party_id": party_id,
                "party_display_name": str(
                    raw.get("party_display_name") or "审稿人"
                ),
                "party_role": str(raw.get("party_role") or ""),
                "replies": [],
            }
        else:
            # 补齐更完整的展示名/角色（首次可能为空）。
            if not groups[party_id]["party_display_name"] and raw.get(
                "party_display_name"
            ):
                groups[party_id]["party_display_name"] = str(
                    raw["party_display_name"]
                )
            if not groups[party_id]["party_role"] and raw.get("party_role"):
                groups[party_id]["party_role"] = str(raw["party_role"])
        groups[party_id]["replies"].append(dict(raw))

    sorted_ids = sorted(
        ordered_ids,
        key=lambda party_id: _party_sort_key(
            party_id,
            role=groups[party_id]["party_role"],
            display_name=groups[party_id]["party_display_name"],
            first_index=first_seen[party_id],
        ),
    )
    result: list[dict[str, Any]] = []
    for party_id in sorted_ids:
        group = groups[party_id]
        numbered: list[dict[str, Any]] = []
        for opinion_no, reply in enumerate(group["replies"], start=1):
            item = dict(reply)
            item["opinion_no"] = opinion_no
            numbered.append(item)
        group["replies"] = numbered
        result.append(group)
    return result


def _reviewer_comment_text(reply: dict[str, Any]) -> str:
    excerpt = str(reply.get("excerpt") or "").strip()
    claim = str(reply.get("localized_claim") or "").strip()
    return excerpt or claim


def _concern_text(reply: dict[str, Any]) -> str | None:
    excerpt = str(reply.get("excerpt") or "").strip()
    claim = str(reply.get("localized_claim") or "").strip()
    if not claim:
        return None
    if claim == excerpt:
        return None
    return claim


def _workspace_title(snapshot: dict[str, Any]) -> str:
    title = str(snapshot.get("workspace_title") or "").strip()
    return title or "审稿回复汇总"


def _docx(snapshot: dict[str, Any], path: Path) -> None:
    title = _workspace_title(snapshot)
    paragraphs = [
        _paragraph(f"{title} · 审稿意见回复"),
        _paragraph("对外回复"),
    ]
    groups = group_external_replies_by_party(snapshot.get("external_replies", []))
    if not groups:
        paragraphs.append(_paragraph("（暂无已批准对外回复）"))
    for group in groups:
        paragraphs.append(_paragraph(str(group["party_display_name"])))
        paragraphs.append(_paragraph("感谢您提出的宝贵意见。我们逐点回复如下："))
        for reply in group["replies"]:
            opinion_no = reply.get("opinion_no", 1)
            paragraphs.append(_paragraph(f"意见 {opinion_no}"))
            paragraphs.append(
                _paragraph(f"审稿意见：{_reviewer_comment_text(reply)}")
            )
            concern = _concern_text(reply)
            if concern:
                paragraphs.append(_paragraph(f"关注点：{concern}"))
            paragraphs.append(_paragraph("回复："))
            paragraphs.append(_paragraph(str(reply.get("content") or "")))

    paragraphs.append(_paragraph("内部修改清单"))
    revision_items = list(snapshot.get("internal_revision_items", []) or [])
    if not revision_items:
        paragraphs.append(_paragraph("（暂无内部修改清单）"))
    for item in revision_items:
        paragraphs.append(_paragraph(f"- {item.get('canonical_text', '')}"))
        for fact in item.get("modification_facts", []) or []:
            paragraphs.append(_paragraph(f"  - {fact}"))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body>'
        + "".join(paragraphs)
        + "<w:sectPr/></w:body></w:document>"
    )
    _atomic_zip(
        path,
        {
            "[Content_Types].xml": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
                'content-types"><Default Extension="rels" '
                'ContentType="application/vnd.openxmlformats-package.'
                'relationships+xml"/><Default Extension="xml" '
                'ContentType="application/xml"/><Override '
                'PartName="/word/document.xml" ContentType="application/'
                'vnd.openxmlformats-officedocument.wordprocessingml.document.'
                'main+xml"/></Types>'
            ),
            "_rels/.rels": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/'
                'package/2006/relationships"><Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/officeDocument" Target="word/document.xml"/>'
                "</Relationships>"
            ),
            "word/document.xml": document,
        },
    )


def _xlsx_cell(column: int, row: int, value: object) -> str:
    letters = ""
    number = column
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    text = escape(str(value or ""))
    return (
        f'<c r="{letters}{row}" t="inlineStr"><is><t xml:space="preserve">'
        f"{text}</t></is></c>"
    )


def _sheet(rows: Iterable[Iterable[object]]) -> str:
    row_xml = []
    for row_number, values in enumerate(rows, start=1):
        cells = "".join(
            _xlsx_cell(column, row_number, value)
            for column, value in enumerate(values, start=1)
        )
        row_xml.append(f'<row r="{row_number}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
        f'2006/main"><sheetData>{"".join(row_xml)}</sheetData></worksheet>'
    )


def _xlsx(snapshot: dict[str, Any], path: Path) -> None:
    revision_rows: list[list[object]] = [
        ["建议", "优先级", "修改事实", "关联来源"]
    ]
    for item in snapshot.get("internal_revision_items", []):
        revision_rows.append(
            [
                item.get("canonical_text", ""),
                item.get("priority", ""),
                "；".join(item.get("modification_facts", [])),
                "；".join(item.get("source_labels", [])),
            ]
        )
    reply_rows: list[list[object]] = [
        ["审稿人", "意见序号", "审稿意见", "关注点", "回复"]
    ]
    groups = group_external_replies_by_party(snapshot.get("external_replies", []))
    for group in groups:
        for reply in group["replies"]:
            reply_rows.append(
                [
                    group["party_display_name"],
                    reply.get("opinion_no", ""),
                    _reviewer_comment_text(reply),
                    _concern_text(reply) or "",
                    str(reply.get("content") or ""),
                ]
            )
    _atomic_zip(
        path,
        {
            "[Content_Types].xml": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
                'content-types"><Default Extension="rels" '
                'ContentType="application/vnd.openxmlformats-package.'
                'relationships+xml"/><Default Extension="xml" '
                'ContentType="application/xml"/><Override '
                'PartName="/xl/workbook.xml" ContentType="application/vnd.'
                'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/'
                'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/'
                'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                "</Types>"
            ),
            "_rels/.rels": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
                '2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
                'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                'Target="xl/workbook.xml"/></Relationships>'
            ),
            "xl/workbook.xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
                '2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/'
                '2006/relationships"><sheets><sheet name="内部修改清单" sheetId="1" '
                'r:id="rId1"/><sheet name="对外回复" sheetId="2" r:id="rId2"/>'
                "</sheets></workbook>"
            ),
            "xl/_rels/workbook.xml.rels": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
                '2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
                'openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                'Target="worksheets/sheet1.xml"/><Relationship Id="rId2" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/worksheet" Target="worksheets/sheet2.xml"/>'
                "</Relationships>"
            ),
            "xl/worksheets/sheet1.xml": _sheet(revision_rows),
            "xl/worksheets/sheet2.xml": _sheet(reply_rows),
        },
    )


def render_export_markdown(snapshot: dict[str, Any]) -> str:
    """从快照字典渲染 Markdown 文本（不落盘）。"""
    title = _workspace_title(snapshot)
    lines = [f"# {title} · 审稿意见回复", "", "## 对外回复", ""]
    groups = group_external_replies_by_party(snapshot.get("external_replies", []))
    if not groups:
        lines.extend(("（暂无已批准对外回复）", ""))
    for group in groups:
        lines.extend(
            (
                f"## {group['party_display_name']}",
                "",
                "感谢您提出的宝贵意见。我们逐点回复如下：",
                "",
            )
        )
        for reply in group["replies"]:
            opinion_no = reply.get("opinion_no", 1)
            lines.extend(
                (
                    f"### 意见 {opinion_no}",
                    f"**审稿意见：** {_reviewer_comment_text(reply)}",
                )
            )
            concern = _concern_text(reply)
            if concern:
                lines.append(f"**关注点：** {concern}")
            lines.extend(
                (
                    "**回复：**",
                    "",
                    str(reply.get("content") or ""),
                    "",
                )
            )

    lines.extend(("## 内部修改清单", ""))
    revision_items = list(snapshot.get("internal_revision_items", []) or [])
    if not revision_items:
        lines.append("（暂无内部修改清单）")
    for item in revision_items:
        lines.append(f"- {item.get('canonical_text', '')}")
        for fact in item.get("modification_facts", []) or []:
            lines.append(f"  - {fact}")
    return "\n".join(lines).rstrip() + "\n"


# 兼容旧调用名（backend 中为模块内 _markdown）
_markdown = render_export_markdown


def generate_export_files(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """生成第 9.5 节三种文件，并返回可审计的文件元数据。

    依赖 snapshot 中至少含 workspace_id / export_snapshot_id；
    不读数据库。导出根目录：langgraph-agent/.tmp/finalize_exports/
    """
    directory = _EXPORT_ROOT / str(snapshot["workspace_id"]) / str(
        snapshot["export_snapshot_id"]
    )
    markdown_path = directory / "review-response.md"
    docx_path = directory / "review-response.docx"
    xlsx_path = directory / "revision-checklist.xlsx"
    _atomic_text(markdown_path, render_export_markdown(snapshot))
    _docx(snapshot, docx_path)
    _xlsx(snapshot, xlsx_path)

    outputs = []
    for format_name, path in (
        ("MARKDOWN", markdown_path),
        ("WORD", docx_path),
        ("EXCEL", xlsx_path),
    ):
        outputs.append(
            {
                "format": format_name,
                "storage_uri": path.relative_to(_PACKAGE_ROOT).as_posix(),
                "content_hash": _hash_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return outputs


__all__ = [
    "export_download_meta",
    "generate_export_files",
    "group_external_replies_by_party",
    "normalize_export_format",
    "render_export_markdown",
    "resolve_registered_export_path",
]
