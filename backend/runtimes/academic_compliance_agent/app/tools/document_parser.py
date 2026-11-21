from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Tuple
from xml.etree import ElementTree

from academic_compliance_agent.app.tools.common import normalize_text, unique_preserve_order


SECTION_RE = re.compile(
    r"^(?:#{1,6}\s*)?(摘要|关键词|Abstract|Keywords|引言|绪论|Introduction|相关工作|Related Work|"
    r"方法|研究方法|材料与方法|Methods|Methodology|实验|实验设计|Experiments|结果|Results|"
    r"讨论|Discussion|结论|Conclusion|致谢|Acknowledg(?:e)?ments|参考文献|References)\b",
    re.IGNORECASE,
)
NUMBERED_SECTION_RE = re.compile(r"^(?:#{1,6}\s*)?\d+(?:\.\d+)*\s+(.{2,40})$")
REFERENCE_HEADING_RE = re.compile(r"^(?:#{1,6}\s*)?(参考文献|References)\b", re.IGNORECASE)
FIGURE_CAPTION_RE = re.compile(r"^(?:图|Figure|Fig\.)\s*([0-9]+)[：:\.\s-]*(.*)", re.IGNORECASE)
TABLE_CAPTION_RE = re.compile(r"^(?:表|Table)\s*([0-9]+)[：:\.\s-]*(.*)", re.IGNORECASE)


class DocumentParserTool:
    """Parse a manuscript into a lightweight structured document."""

    def parse_file(self, file_path: str) -> Dict[str, Any]:
        path = Path(file_path)
        suffix = path.suffix.lower()
        if suffix in {".md", ".markdown", ".txt"}:
            text = path.read_text(encoding="utf-8")
        elif suffix == ".docx":
            text = self._read_docx(path)
        elif suffix == ".pdf":
            text = self._read_pdf(path)
        else:
            text = path.read_text(encoding="utf-8", errors="ignore")
        return self.parse_text(text, source_path=str(path))

    def parse_text(self, text: str, source_path: str = "") -> Dict[str, Any]:
        lines = text.splitlines()
        sections = self._extract_sections(lines)
        references = self._extract_references(lines)
        citations = self._extract_citations(text)
        figures, tables = self._extract_figures_tables(lines)
        statements = self._extract_statements(sections, text)
        title = self._extract_title(lines, sections)
        abstract = self._section_content(sections, ["摘要", "Abstract"])
        keywords = self._extract_keywords(lines)
        body_text = "\n".join(line for line in lines if not REFERENCE_HEADING_RE.match(line.strip()))

        return {
            "source_path": source_path,
            "raw_text": text,
            "body_text": body_text,
            "title": title,
            "abstract": abstract,
            "keywords": keywords,
            "sections": sections,
            "figures": figures,
            "tables": tables,
            "references": references,
            "citations": citations,
            "statements": statements,
            "submission_files": [],
        }

    def _read_docx(self, path: Path) -> str:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for paragraph in root.findall(".//w:p", namespace):
            texts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
            line = "".join(texts).strip()
            if line:
                paragraphs.append(line)
        return "\n".join(paragraphs)

    def _read_pdf(self, path: Path) -> str:
        errors: List[str] = []

        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(str(path))
            pages = []
            for index, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(f"\n\n--- Page {index} ---\n{text.strip()}")
            if pages:
                return "\n".join(pages)
        except Exception as exc:  # pragma: no cover - depends on optional PDF backend.
            errors.append(f"pypdf: {exc}")

        try:
            from PyPDF2 import PdfReader  # type: ignore

            reader = PdfReader(str(path))
            pages = []
            for index, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(f"\n\n--- Page {index} ---\n{text.strip()}")
            if pages:
                return "\n".join(pages)
        except Exception as exc:  # pragma: no cover - depends on optional PDF backend.
            errors.append(f"PyPDF2: {exc}")

        try:
            import pdfplumber  # type: ignore

            pages = []
            with pdfplumber.open(str(path)) as pdf:
                for index, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text() or ""
                    if text.strip():
                        pages.append(f"\n\n--- Page {index} ---\n{text.strip()}")
            if pages:
                return "\n".join(pages)
        except Exception as exc:  # pragma: no cover - depends on optional PDF backend.
            errors.append(f"pdfplumber: {exc}")

        detail = "; ".join(errors) if errors else "no PDF extraction backend is installed"
        raise RuntimeError(
            "PDF parsing failed. Please install PDF dependencies with "
            "`pip install -r requirements.txt` or `pip install pypdf pdfplumber`. "
            f"Details: {detail}"
        )

    def _extract_title(self, lines: List[str], sections: List[Dict[str, Any]]) -> str:
        for line in lines:
            value = line.strip().lstrip("#").strip()
            if not value:
                continue
            if SECTION_RE.match(value) or REFERENCE_HEADING_RE.match(value):
                continue
            return value[:120]
        return sections[0]["title"] if sections else ""

    def _extract_sections(self, lines: List[str]) -> List[Dict[str, Any]]:
        sections: List[Dict[str, Any]] = []
        current_title = "正文"
        current_start = 1
        buffer: List[str] = []

        def flush(end_line: int) -> None:
            if buffer or current_title != "正文":
                sections.append(
                    {
                        "title": current_title,
                        "content": "\n".join(buffer).strip(),
                        "start_line": current_start,
                        "end_line": end_line,
                    }
                )

        for index, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            heading = self._heading_title(line)
            if heading:
                flush(index - 1)
                current_title = heading
                current_start = index
                inline_content = self._inline_heading_content(line)
                buffer = [inline_content] if inline_content else []
            else:
                buffer.append(raw_line)
        flush(len(lines))
        return [section for section in sections if section["content"] or section["title"] != "正文"]

    def _heading_title(self, line: str) -> str:
        if not line:
            return ""
        normalized = line.lstrip("#").strip()
        section = SECTION_RE.match(normalized)
        if section:
            return section.group(1).rstrip("：:")
        numbered = NUMBERED_SECTION_RE.match(normalized)
        if numbered and len(normalized) <= 60:
            return normalized
        return ""

    def _inline_heading_content(self, line: str) -> str:
        normalized = line.lstrip("#").strip()
        section = SECTION_RE.match(normalized)
        if not section:
            return ""
        rest = normalized[section.end():].lstrip("：: \t")
        return rest.strip()

    def _section_content(self, sections: List[Dict[str, Any]], names: List[str]) -> str:
        lowered = [name.lower() for name in names]
        for section in sections:
            title = section["title"].lower()
            if any(name.lower() in title for name in lowered):
                return section.get("content", "")
        return ""

    def _extract_keywords(self, lines: List[str]) -> List[str]:
        for line in lines:
            stripped = line.strip()
            if re.match(r"^(关键词|Keywords)\s*[:：]", stripped, re.IGNORECASE):
                _, value = re.split(r"[:：]", stripped, maxsplit=1)
                parts = re.split(r"[；;，,、]", value)
                return [part.strip() for part in parts if part.strip()]
        return []

    def _extract_references(self, lines: List[str]) -> List[Dict[str, Any]]:
        references: List[Dict[str, Any]] = []
        in_references = False
        current = ""
        current_number = None
        for line in lines:
            stripped = line.strip()
            if REFERENCE_HEADING_RE.match(stripped):
                in_references = True
                continue
            if not in_references or not stripped:
                continue
            numbered = re.match(r"^\[?(\d+)\]?[\.、]?\s*(.+)", stripped)
            if numbered:
                if current:
                    references.append({"number": current_number, "text": current.strip()})
                current_number = int(numbered.group(1))
                current = numbered.group(2)
            else:
                current = f"{current} {stripped}".strip()
        if current:
            references.append({"number": current_number, "text": current.strip()})
        return references

    def _extract_citations(self, text: str) -> List[Dict[str, Any]]:
        citations: List[Dict[str, Any]] = []
        numbers: List[int] = []
        for match in re.finditer(r"\[([0-9,\-，、\s]+)\]", text):
            raw = match.group(1)
            for item in re.split(r"[,，、]\s*", raw):
                item = item.strip()
                if not item:
                    continue
                if "-" in item:
                    left, right = item.split("-", 1)
                    if left.strip().isdigit() and right.strip().isdigit():
                        numbers.extend(range(int(left), int(right) + 1))
                elif item.isdigit():
                    numbers.append(int(item))
        for number in unique_preserve_order(numbers):
            citations.append({"number": number, "style": "numeric"})
        for match in re.finditer(r"\(([^()]{2,80}?\d{4}[^()]*)\)", text):
            citations.append({"text": normalize_text(match.group(1)), "style": "author_year"})
        return citations

    def _extract_figures_tables(self, lines: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        figures: List[Dict[str, Any]] = []
        tables: List[Dict[str, Any]] = []
        for index, line in enumerate(lines, start=1):
            stripped = line.strip()
            figure = FIGURE_CAPTION_RE.match(stripped)
            table = TABLE_CAPTION_RE.match(stripped)
            if figure:
                figures.append({"number": int(figure.group(1)), "caption": figure.group(2).strip(), "line": index})
            if table:
                tables.append({"number": int(table.group(1)), "caption": table.group(2).strip(), "line": index})
        return figures, tables

    def _extract_statements(self, sections: List[Dict[str, Any]], text: str) -> Dict[str, str]:
        statement_titles = {
            "academic_integrity": ["学术诚信", "诚信声明", "原创性声明"],
            "funding": ["基金", "资助", "Funding"],
            "conflict_of_interest": ["利益冲突", "Conflict of Interest"],
        }
        statements: Dict[str, str] = {key: "" for key in statement_titles}
        for key, names in statement_titles.items():
            for section in sections:
                title = section.get("title", "")
                if any(name.lower() in title.lower() for name in names):
                    statements[key] = section.get("content", "")
            if not statements[key]:
                for name in names:
                    match = re.search(rf"({re.escape(name)}\s*[:：].{{0,300}})", text, re.IGNORECASE)
                    if match:
                        statements[key] = match.group(1)
                        break
        return statements
