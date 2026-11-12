"""使用 PyMuPDF4LLM 将论文 PDF 转换为精简结构。

来源：backend/app/parsing/pdf_parser.py
"""

from __future__ import annotations

import re
from pathlib import Path

from langgraph_agent.tools.paper_schemas import ParsedPaper, PaperSection, SectionType


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SECTION_NUMBER_RE = re.compile(
    r"^\s*(?:"
    r"(?P<alpha_hier>[A-Z](?:\.\d+)+)(?:[.)、:：-]|(?=\s|$))"
    r"|(?P<alpha_root>[A-Z])[.)、:：-]"
    r"|(?P<numeric>\d+(?:\.\d+)*|[一二三四五六七八九十]+)"
    r"(?:[.)、:：-]|(?=\s|$))"
    r")",
    re.I,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MARKDOWN_EMPHASIS_RE = re.compile(r"(?<!\\)[*_]{1,3}")
_SENTENCE_END_RE = re.compile(r"[.!?。！？](?:[\"'”’»）》】)\]]*)")
_EXCERPT_LIMIT = 400
_TYPE_RULES: tuple[tuple[SectionType, tuple[str, ...]], ...] = (
    (SectionType.ABSTRACT, ("abstract", "摘要")),
    (SectionType.INTRODUCTION, ("introduction", "background", "引言", "绪论", "研究背景")),
    (SectionType.RELATED_WORK, ("related work", "literature review", "相关工作", "文献综述")),
    (SectionType.ABLATION, ("ablation", "消融", "supplementary analysis", "补充分析")),
    (SectionType.LIMITATIONS, ("limitation", "limitations", "局限", "局限性")),
    (SectionType.DATASET, ("dataset", "data set", "data and sample", "数据集", "数据与样本", "研究样本")),
    (SectionType.EXPERIMENTS, ("experiment", "experimental setup", "evaluation", "实验", "评价")),
    (SectionType.RESULTS, ("result", "findings", "结果", "研究发现")),
    (SectionType.METHOD, ("method", "methodology", "approach", "materials and methods", "方法", "研究方法")),
    (SectionType.DISCUSSION, ("discussion", "讨论")),
    (SectionType.CONCLUSION, ("conclusion", "conclusions", "结论")),
    (SectionType.REFERENCES, ("references", "bibliography", "参考文献")),
)


def normalize_section_type(heading: str) -> tuple[SectionType, float]:
    number_match = _SECTION_NUMBER_RE.match(heading)
    normalized = (
        heading[number_match.end():]
        if number_match is not None
        else heading
    ).strip().lower()
    for section_type, keywords in _TYPE_RULES:
        if any(keyword in normalized for keyword in keywords):
            return section_type, 0.95
    return SectionType.OTHER, 0.5


def _clean_heading(heading: str) -> str:
    """移除 PyMuPDF4LLM 标题中的展示标记，保留可读标题文本。"""
    without_html = _HTML_TAG_RE.sub("", heading)
    without_emphasis = _MARKDOWN_EMPHASIS_RE.sub("", without_html)
    return re.sub(r"\s+", " ", without_emphasis).strip()


def _page_number(chunk: dict, fallback: int) -> int:
    value = chunk.get("metadata", {}).get("page_number", fallback)
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _section_excerpt(text: str, limit: int = _EXCERPT_LIMIT) -> str:
    """压缩章节正文并生成不保存完整正文的短预览。"""
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact

    boundary = 0
    for match in _SENTENCE_END_RE.finditer(compact[:limit]):
        boundary = match.end()
    if boundary >= limit // 2:
        return compact[:boundary].strip()

    # 找不到合适的句末时保留上限内的前缀，并明确这是截断预览。
    return f"{compact[: max(0, limit - 1)].rstrip()}…"


def _numbering_parts(heading: str) -> tuple[str, ...] | None:
    match = _SECTION_NUMBER_RE.match(heading)
    if match is None:
        return None
    number = next(
        value
        for value in match.group("alpha_hier", "alpha_root", "numeric")
        if value is not None
    )
    return tuple(part.upper() for part in number.split("."))


def _same_heading(left: str, right: str) -> bool:
    return re.sub(r"\s+", " ", left).strip().casefold() == re.sub(
        r"\s+", " ", right
    ).strip().casefold()


def _annotate_sections(
    sections: list[PaperSection],
    markdown_levels: dict[int, int] | None = None,
) -> None:
    """按编号层级优先、Markdown 层级补充的规则建立树形大纲。"""
    numbered_by_path: dict[tuple[str, ...], PaperSection] = {}
    markdown_stack: list[tuple[PaperSection, int]] = []
    numbered_context: tuple[PaperSection, int] | None = None
    context_stack: list[tuple[PaperSection, int]] = []

    for order_index, section in enumerate(sections):
        markdown_level = (
            markdown_levels.get(id(section), section.level or 1)
            if markdown_levels is not None
            else section.level or 1
        )
        section.section_id = f"section-{order_index + 1:04d}"
        section.order_index = order_index
        section.excerpt = _section_excerpt(section.text)

        numbering = _numbering_parts(section.original_heading)
        if numbering is not None:
            for path in list(numbered_by_path):
                if path[0] != numbering[0] or len(path) >= len(numbering):
                    numbered_by_path.pop(path)
            parent = (
                numbered_by_path.get(numbering[:-1])
                if len(numbering) > 1
                else None
            )
            section.parent_id = parent.section_id if parent else None
            section.level = len(numbering) + 1
            numbered_by_path[numbering] = section
            numbered_context = (section, markdown_level)
            context_stack = []
            continue

        if (
            numbered_context is not None
            and markdown_level > 2
            and markdown_level >= numbered_context[1]
        ):
            while context_stack and context_stack[-1][1] >= markdown_level:
                context_stack.pop()
            parent = context_stack[-1][0] if context_stack else numbered_context[0]
            section.parent_id = parent.section_id
            section.level = max(markdown_level, (parent.level or 1) + 1)
            context_stack.append((section, markdown_level))
            continue

        numbered_context = None
        context_stack = []
        while markdown_stack and markdown_stack[-1][1] >= markdown_level:
            markdown_stack.pop()
        parent = markdown_stack[-1][0] if markdown_stack else None
        section.parent_id = parent.section_id if parent else None
        section.level = markdown_level
        markdown_stack.append((section, markdown_level))


def _filter_empty_leaf_sections(
    sections: list[PaperSection],
    *,
    title: str,
    markdown_levels: dict[int, int],
) -> list[PaperSection]:
    """过滤空伪标题，同时保留拥有正文后代的空结构容器。"""
    _annotate_sections(sections, markdown_levels)
    by_id = {
        section.section_id: section
        for section in sections
        if section.section_id is not None
    }
    kept_ids = {
        section.section_id
        for section in sections
        if section.text and section.section_id is not None
    }

    for section in sections:
        if not section.text:
            continue
        parent_id = section.parent_id
        while parent_id is not None:
            parent = by_id[parent_id]
            if not (
                not parent.text
                and title
                and _same_heading(parent.original_heading, title)
            ):
                kept_ids.add(parent_id)
            parent_id = parent.parent_id

    filtered = [
        section
        for section in sections
        if section.section_id in kept_ids
    ]
    _annotate_sections(filtered, markdown_levels)
    return filtered


def _markdown_sections(chunks: list[dict]) -> tuple[str, list[PaperSection]]:
    title = ""
    sections: list[PaperSection] = []
    current_heading = ""
    current_type = SectionType.OTHER
    current_confidence = 0.5
    current_level: int | None = None
    current_lines: list[str] = []
    current_pages: list[int] = []

    def flush() -> None:
        nonlocal current_lines, current_pages, current_level
        text = "\n".join(current_lines).strip()
        if current_heading:
            sections.append(
                PaperSection(
                    original_heading=current_heading,
                    normalized_type=current_type,
                    text=text,
                    pages=list(dict.fromkeys(current_pages)),
                    confidence=current_confidence,
                    level=current_level,
                )
            )
        current_lines = []
        current_pages = []
        current_level = None

    for index, chunk in enumerate(chunks, start=1):
        page = _page_number(chunk, index)
        for line in str(chunk.get("text", "")).splitlines():
            match = _HEADING_RE.match(line.strip())
            if match:
                heading = _clean_heading(match.group(2))
                if not title and match.group(1) == "#":
                    title = heading
                    continue
                if title and _same_heading(heading, title):
                    # 跨页重复论文标题属于页眉，忽略标题标记并继续当前章节正文。
                    continue
                flush()
                current_heading = heading
                current_type, current_confidence = normalize_section_type(heading)
                current_level = len(match.group(1))
                current_pages = [page]
                continue
            if current_heading:
                current_lines.append(line)
                current_pages.append(page)
    flush()
    return title, sections


def _plain_text_fallback(document, warning: str) -> ParsedPaper:
    page_texts = [page.get_text("text").strip() for page in document]
    full_text = "\n\n".join(text for text in page_texts if text).strip()
    warnings = [warning]
    if not full_text:
        warnings.append("UNREADABLE_PDF: PDF 中未检测到可读取文本，请更换文本型 PDF 或其他格式")
        return ParsedPaper("", "", "", [], warnings)
    warnings.append("DEGRADED_FULL_TEXT: 章节结构不可用，已保留全文纯文本供人工检查")
    return ParsedPaper("", "", full_text, [], warnings)


def _build_result(chunks: list[dict]) -> ParsedPaper:
    full_text = "\n\n".join(str(chunk.get("text", "")).strip() for chunk in chunks).strip()
    title, raw_sections = _markdown_sections(chunks)

    # 先在完整标题序列上建立临时树，避免把无正文但拥有子章节的合法容器误删。
    # 作者名、页眉和重复论文标题等没有正文后代的空叶节点仍会被过滤。
    markdown_levels = {
        id(section): section.level or 1
        for section in raw_sections
    }
    sections = _filter_empty_leaf_sections(
        raw_sections,
        title=title,
        markdown_levels=markdown_levels,
    )

    abstract_section = next(
        (section for section in sections if section.normalized_type is SectionType.ABSTRACT),
        None,
    )
    warnings: list[str] = []
    if not title:
        warnings.append("MISSING_TITLE: 未可靠识别论文标题")
    if abstract_section is None:
        warnings.append("MISSING_ABSTRACT: 未可靠识别摘要")
    major_sections = [section for section in sections if section.normalized_type is not SectionType.OTHER]
    if not sections or len(major_sections) < 2:
        warnings.append("DEGRADED_STRUCTURE: 主要章节识别不完整，请重点检查全文和信息卡片")
    if not full_text:
        warnings.append("UNREADABLE_PDF: PDF 中未检测到可读取文本，请更换文本型 PDF 或其他格式")
    return ParsedPaper(
        title=title,
        abstract=abstract_section.text if abstract_section else "",
        full_text=full_text,
        sections=sections,
        parse_warnings=warnings,
    )


def parse_pdf(source: str | Path | bytes) -> ParsedPaper:
    """解析 PDF 路径或字节；失败时尽可能保留可读取的原始文本。"""
    try:
        import pymupdf
        import pymupdf4llm
    except ImportError as error:
        return ParsedPaper(
            "",
            "",
            "",
            [],
            [f"PARSER_UNAVAILABLE: PyMuPDF4LLM 依赖不可用（{error}）"],
        )

    document = None
    try:
        if isinstance(source, bytes):
            document = pymupdf.open(stream=source, filetype="pdf")
        else:
            document = pymupdf.open(str(Path(source)))
        chunks = pymupdf4llm.to_markdown(document, page_chunks=True, show_progress=False)
        if not isinstance(chunks, list):
            raise TypeError("page_chunks=True 未返回分页列表")
        result = _build_result(chunks)
        if result.full_text:
            return result
        return _plain_text_fallback(document, "DEGRADED_PARSER: Markdown 解析未返回正文")
    except Exception as error:  # PDF/解析库异常统一进入明确降级路径
        if document is not None:
            try:
                return _plain_text_fallback(
                    document,
                    f"DEGRADED_PARSER: PyMuPDF4LLM 解析失败（{type(error).__name__}: {error}）",
                )
            except Exception:
                pass
        return ParsedPaper(
            "",
            "",
            "",
            [],
            [f"UNREADABLE_PDF: PDF 无法读取（{type(error).__name__}: {error}）"],
        )
    finally:
        if document is not None:
            document.close()


def build_parsed_paper_from_markdown_chunks(chunks: list[dict]) -> ParsedPaper:
    """测试/离线入口：直接从 page_chunks 结构构造 ParsedPaper，不依赖真实 PDF。"""
    return _build_result(chunks)


__all__ = [
    "build_parsed_paper_from_markdown_chunks",
    "normalize_section_type",
    "parse_pdf",
]
