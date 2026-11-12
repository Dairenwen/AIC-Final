from __future__ import annotations

import csv
import importlib.util
import re
from collections import Counter
from functools import lru_cache
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, g, request, send_file

from ..extensions import db
from ..models import Task
from ..tools.literature_ppt import LiteraturePptService
from .tasks import serialize_task

from .responses import error, ok


bp = Blueprint("research_tools", __name__)
MAX_TEXT_LENGTH = 200_000
MAX_TABLE_ROWS = 200
MAX_TABLE_COLUMNS = 40
ALLOWED_ENTRY_TYPES = {"article", "inproceedings", "book", "thesis", "misc"}


@bp.post("/tools/citation-formatter/format")
def format_citation():
    payload = request.get_json(silent=True) or {}
    title = _required_text(payload, "title", "论文标题", 500)
    venue = _required_text(payload, "venue", "期刊或会议名称", 300)
    authors = _authors(payload.get("authors"))
    if isinstance(title, tuple):
        return title
    if isinstance(venue, tuple):
        return venue
    if isinstance(authors, tuple):
        return authors

    try:
        year = int(payload.get("year"))
    except (TypeError, ValueError):
        return error("请输入有效发表年份", code="CITATION_YEAR_INVALID", status=400)
    if year < 1000 or year > 2200:
        return error("发表年份应在 1000 至 2200 之间", code="CITATION_YEAR_INVALID", status=400)

    entry_type = str(payload.get("entryType") or "article").strip().lower()
    if entry_type not in ALLOWED_ENTRY_TYPES:
        return error("不支持该文献类型", code="CITATION_TYPE_INVALID", status=400)
    doi = _optional_text(payload.get("doi"), 300)
    volume = _optional_text(payload.get("volume"), 50)
    pages = _optional_text(payload.get("pages"), 50)
    cite_key = _citation_key(authors[0], year, title)
    bibtex = _bibtex(entry_type, cite_key, title, authors, year, venue, doi, volume, pages)
    return ok(
        {
            "bibtex": bibtex,
            "apa": _apa(authors, year, title, venue, volume, pages, doi),
            "gbt7714": _gbt7714(entry_type, authors, year, title, venue, volume, pages, doi),
            "inline": f"({_family_name(authors[0])} et al., {year})" if len(authors) > 2 else f"({_inline_authors(authors)}, {year})",
            "citationKey": cite_key,
        }
    )


@bp.post("/tools/table-converter/convert")
def convert_table():
    payload = request.get_json(silent=True) or {}
    source = str(payload.get("source") or "")
    if not source.strip():
        return error("请输入 CSV 或 TSV 表格数据", code="TABLE_SOURCE_REQUIRED", status=400)
    if len(source) > MAX_TEXT_LENGTH:
        return error("表格文本超过 200000 字符限制", code="TABLE_SOURCE_TOO_LARGE", status=413)

    delimiter_mode = str(payload.get("delimiter") or "auto").lower()
    delimiters = {"comma": ",", "tab": "\t", "semicolon": ";"}
    delimiter = delimiters.get(delimiter_mode)
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(source[:4096], delimiters=",\t;").delimiter
        except csv.Error:
            delimiter = "\t" if "\t" in source else ","
    try:
        rows = [[cell.strip() for cell in row] for row in csv.reader(StringIO(source), delimiter=delimiter)]
    except csv.Error:
        return error("表格数据格式无法解析", code="TABLE_SOURCE_INVALID", status=400)
    rows = [row for row in rows if any(cell for cell in row)]
    if len(rows) < 1 or len(rows[0]) < 1:
        return error("未解析到有效表格数据", code="TABLE_SOURCE_INVALID", status=400)
    if len(rows) > MAX_TABLE_ROWS or max(map(len, rows)) > MAX_TABLE_COLUMNS:
        return error("单次最多转换 200 行、40 列", code="TABLE_SIZE_EXCEEDED", status=413)
    width = max(map(len, rows))
    normalized = [row + [""] * (width - len(row)) for row in rows]
    caption = _optional_text(payload.get("caption"), 300) or "实验结果"
    label = _slug(_optional_text(payload.get("label"), 100) or "results")
    return ok(
        {
            "markdown": _markdown_table(normalized),
            "latex": _latex_table(normalized, caption, label),
            "preview": normalized[:20],
            "rowCount": len(normalized),
            "columnCount": width,
            "delimiter": {",": "comma", "\t": "tab", ";": "semicolon"}.get(delimiter, "comma"),
        }
    )


@bp.post("/tools/text-statistics/analyze")
def analyze_text():
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text") or "")
    if not text.strip():
        return error("请输入需要分析的文本", code="TEXT_REQUIRED", status=400)
    if len(text) > MAX_TEXT_LENGTH:
        return error("文本超过 200000 字符限制", code="TEXT_TOO_LARGE", status=413)

    chinese = re.findall(r"[\u4e00-\u9fff]", text)
    english_words = re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)*", text)
    numbers = re.findall(r"\b\d+(?:\.\d+)?\b", text)
    paragraphs = [item for item in re.split(r"\n\s*\n|\r\n\s*\r\n", text) if item.strip()]
    sentences = [item for item in re.split(r"(?<=[。！？!?\.])\s*", text.strip()) if item.strip()]
    academic_units = len(chinese) + len(english_words)
    keywords = _top_keywords(text)
    return ok(
        {
            "characters": len(text),
            "charactersNoSpaces": len(re.sub(r"\s", "", text)),
            "chineseCharacters": len(chinese),
            "englishWords": len(english_words),
            "numbers": len(numbers),
            "paragraphs": len(paragraphs),
            "sentences": len(sentences),
            "estimatedReadingMinutes": max(1, round(academic_units / 350)),
            "averageSentenceLength": round(academic_units / max(1, len(sentences)), 1),
            "keywords": [{"term": term, "count": count} for term, count in keywords],
        }
    )


@bp.post("/tools/markdown-to-docx/export")
def export_markdown_docx():
    payload = request.get_json(silent=True) or {}
    markdown = str(payload.get("markdown") or "")
    if not markdown.strip():
        return error("请输入需要导出的 Markdown 文稿", code="MARKDOWN_REQUIRED", status=400)
    if len(markdown) > MAX_TEXT_LENGTH:
        return error("Markdown 超过 200000 字符限制", code="MARKDOWN_TOO_LARGE", status=413)
    filename = _safe_filename(str(payload.get("filename") or "科研文稿")) + ".docx"
    try:
        converter = _load_docx_converter()
        document = converter(markdown, None)
        stream = BytesIO()
        document.save(stream)
        stream.seek(0)
    except Exception:
        current_app.logger.exception("Markdown to DOCX conversion failed")
        return error("Word 文档生成失败，请检查 Markdown 格式", code="DOCX_EXPORT_FAILED", status=500)
    return send_file(
        stream,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@bp.post("/tools/literature-ppt/generate")
def generate_literature_ppt():
    file = request.files.get("file")
    if file is None or not file.filename:
        return error("请选择 PDF 文献", code="LITERATURE_PPT_FILE_REQUIRED", status=400)
    if Path(file.filename).suffix.lower() != ".pdf":
        return error("文献 PPT 工具仅支持 PDF 文件", code="LITERATURE_PPT_FILE_TYPE_INVALID", status=415)
    max_bytes = int(current_app.config["LITERATURE_PPT_UPLOAD_MAX_BYTES"])
    content = file.read(max_bytes + 1)
    if len(content) > max_bytes:
        return error("PDF 文件超过大小限制", code="LITERATURE_PPT_FILE_TOO_LARGE", status=413)
    if not content.startswith(b"%PDF-"):
        return error("文件不是有效的 PDF", code="LITERATURE_PPT_FILE_INVALID", status=415)
    try:
        options = normalize_literature_ppt_options(request.form)
    except ValueError as exc:
        return error(str(exc), code="LITERATURE_PPT_OPTIONS_INVALID", status=400)

    task = Task(
        user_id=g.current_user.id,
        task_type="LITERATURE_PPT",
        status="QUEUED",
        progress=0,
        current_step="等待解析文献",
        input_json={
            "prompt": f"根据文献 {Path(file.filename).name} 生成科研汇报 PPT",
            "source_file": Path(file.filename).name,
            "ppt_options": options,
        },
        output_json={},
        trace_summary={},
    )
    db.session.add(task)
    db.session.flush()
    user_dir = Path(current_app.config["LITERATURE_PPT_UPLOAD_DIR"]) / str(g.current_user.id)
    user_dir.mkdir(parents=True, exist_ok=True)
    source_path = user_dir / f"{task.id}.pdf"
    source_path.write_bytes(content)
    task.input_json = {**task.input_json, "source_path": str(source_path)}
    db.session.commit()
    service = current_app.extensions.get("literature_ppt_service")
    if service is None:
        service = LiteraturePptService(current_app._get_current_object())
        current_app.extensions["literature_ppt_service"] = service
    service.start(task.id, g.current_user.id)
    return ok(serialize_task(task), status=201)


def normalize_literature_ppt_options(form) -> dict[str, Any]:
    raw_slides = str(form.get("slides") or "").strip()
    slides = None
    if raw_slides:
        try:
            slides = int(raw_slides)
        except ValueError as exc:
            raise ValueError("PPT 页数必须是整数") from exc
        if not 3 <= slides <= 30:
            raise ValueError("PPT 页数必须在 3 到 30 页之间")
    values = {
        "audience": _optional_text(form.get("audience"), 120),
        "slides": slides,
        "language": _optional_text(form.get("language"), 40),
        "tone": _optional_text(form.get("tone"), 120),
        "focus": _optional_text(form.get("focus"), 500),
        "requirements": _optional_text(form.get("requirements"), 1000),
    }
    return values


def _required_text(payload: dict[str, Any], key: str, label: str, max_length: int):
    value = str(payload.get(key) or "").strip()
    if not value:
        return error(f"请输入{label}", code=f"{key.upper()}_REQUIRED", status=400)
    if len(value) > max_length:
        return error(f"{label}内容过长", code=f"{key.upper()}_TOO_LONG", status=400)
    return value


def _optional_text(value: Any, max_length: int) -> str:
    return str(value or "").strip()[:max_length]


def _authors(value: Any):
    if isinstance(value, list):
        authors = [str(item).strip() for item in value if str(item).strip()]
    else:
        authors = [item.strip() for item in re.split(r"\s*(?:;|；|\band\b)\s*", str(value or ""), flags=re.I) if item.strip()]
    if not authors:
        return error("请输入至少一位作者", code="CITATION_AUTHORS_REQUIRED", status=400)
    if len(authors) > 50 or any(len(item) > 120 for item in authors):
        return error("作者信息过长", code="CITATION_AUTHORS_INVALID", status=400)
    return authors


def _family_name(author: str) -> str:
    if "," in author:
        return author.split(",", 1)[0].strip()
    parts = author.split()
    return parts[-1] if parts else author


def _citation_key(author: str, year: int, title: str) -> str:
    words = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", title)
    return _slug(f"{_family_name(author)}{year}{words[0] if words else 'work'}")


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff_-]+", "", value)
    return slug[:80] or "research"


def _bib_escape(value: str) -> str:
    return value.replace("\\", "\\textbackslash ").replace("{", "\\{").replace("}", "\\}")


def _bibtex(entry_type: str, key: str, title: str, authors: list[str], year: int, venue: str, doi: str, volume: str, pages: str) -> str:
    venue_key = "journal" if entry_type == "article" else "booktitle"
    lines = [f"@{entry_type}{{{key},", f"  title = {{{_bib_escape(title)}}},", f"  author = {{{' and '.join(map(_bib_escape, authors))}}},", f"  year = {{{year}}},", f"  {venue_key} = {{{_bib_escape(venue)}}},"]
    for field, value in (("volume", volume), ("pages", pages), ("doi", doi)):
        if value:
            lines.append(f"  {field} = {{{_bib_escape(value)}}},")
    lines.append("}")
    return "\n".join(lines)


def _inline_authors(authors: list[str]) -> str:
    return " & ".join(_family_name(item) for item in authors)


def _apa(authors: list[str], year: int, title: str, venue: str, volume: str, pages: str, doi: str) -> str:
    suffix = f", {volume}" if volume else ""
    suffix += f", {pages}" if pages else ""
    suffix += f". https://doi.org/{doi.removeprefix('https://doi.org/')}" if doi else "."
    return f"{', '.join(authors)}. ({year}). {title}. {venue}{suffix}"


def _gbt7714(entry_type: str, authors: list[str], year: int, title: str, venue: str, volume: str, pages: str, doi: str) -> str:
    marker = "J" if entry_type == "article" else "C"
    author_text = ", ".join(authors)
    details = f"{venue}, {year}"
    if volume:
        details += f", {volume}"
    if pages:
        details += f": {pages}"
    if doi:
        details += f". DOI:{doi.removeprefix('https://doi.org/')}"
    return f"{author_text}. {title}[{marker}]. {details}."


def _md_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _markdown_table(rows: list[list[str]]) -> str:
    headers = rows[0]
    body = rows[1:]
    lines = ["| " + " | ".join(map(_md_escape, headers)) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(map(_md_escape, row)) + " |" for row in body)
    return "\n".join(lines)


def _tex_escape(value: str) -> str:
    translations = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(translations.get(char, char) for char in value.replace("\n", " "))


def _latex_table(rows: list[list[str]], caption: str, label: str) -> str:
    width = len(rows[0])
    row_ending = " \\\\"
    lines = [r"\begin{table}[htbp]", f"\\caption{{{_tex_escape(caption)}}}", f"\\label{{tab:{label}}}", r"\centering", f"\\begin{{tabular}}{{{'l' + 'c' * (width - 1)}}}", r"\toprule", " & ".join(map(_tex_escape, rows[0])) + row_ending, r"\midrule"]
    lines.extend(" & ".join(map(_tex_escape, row)) + row_ending for row in rows[1:])
    lines.extend([r"\bottomrule", r"\end{tabular}", r"\end{table}"])
    return "\n".join(lines)


def _top_keywords(text: str) -> list[tuple[str, int]]:
    stopwords = {"the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "of", "to", "in", "a", "an", "is", "on", "本文", "研究", "方法", "结果", "以及", "进行", "通过", "一种", "基于"}
    english = [item.lower() for item in re.findall(r"[A-Za-z]{3,}", text)]
    chinese = re.findall(r"[\u4e00-\u9fff]{2,6}", text)
    return Counter(item for item in [*english, *chinese] if item not in stopwords).most_common(12)


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value).strip(" ._")
    return cleaned[:80] or "科研文稿"


@lru_cache(maxsize=1)
def _load_docx_converter():
    path = Path(__file__).resolve().parents[1] / "agents" / "patent_drafting" / "runtime" / "vendor" / "patent-disclosure-skill" / "tools" / "md_to_docx.py"
    spec = importlib.util.spec_from_file_location("zhiyan_md_to_docx", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Markdown converter is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.convert_md_to_docx
