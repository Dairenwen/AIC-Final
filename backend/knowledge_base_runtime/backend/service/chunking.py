from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from knowledge_base_runtime.backend.dao.database import get_db, utc_now
from knowledge_base_runtime.backend.client.local_splitters import build_local_chunk_records
from knowledge_base_runtime.backend.client.retrieval_backends import delete_chunks_milvus, index_chunks_milvus


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SUPPORTED_STRATEGIES = {
    "fixed_boundary_v1": "fixed_boundary_v1",
    "paragraph_sentence_v1": "paragraph_sentence_v1",
    "section_parent_child_v1": "section_parent_child_v1",
    "semantic_bge_m3_v1": "semantic_bge_m3_v1",
}
SECTION_PREFIX = r"((\d+|[ivxlcdm]+)\.?\s*)?"
START_HEADING = re.compile(rf"(?im)^\s*{SECTION_PREFIX}(introduction|background|overview)\s*$")
END_HEADING = re.compile(rf"(?im)^\s*{SECTION_PREFIX}(references|bibliography)\s*$")
SOFT_HYPHEN_LINEBREAK = re.compile(r"([A-Za-z])-\n(?=[a-z])")
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MANY_SPACES = re.compile(r"[ \t]+")
MANY_BLANK_LINES = re.compile(r"\n{3,}")
MOJIBAKE_REPLACEMENTS = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u00d7": "x",
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
}
MOJIBAKE_MARKER = re.compile("[\ufffd\ufb00-\ufb04]")


@dataclass(frozen=True)
class FulltextExtraction:
    raw_text: str
    clean_text: str
    extraction_method: str
    clean_strategy: str
    mojibake_hits: int


def extract_text_from_pdf_bytes(data: bytes) -> str:
    return extract_fulltext_from_pdf_bytes(data).clean_text


def extract_fulltext_from_pdf_bytes(data: bytes) -> FulltextExtraction:
    raw_text = _extract_pdf_text_pymupdf(data)
    method = "pymupdf" if raw_text else "fallback_decode"
    if not raw_text:
        raw_text = data.decode("utf-8", errors="ignore")
        if len(raw_text.strip()) < 40:
            raw_text = data.decode("latin-1", errors="ignore")
    raw_text = normalize_pdf_text(raw_text)
    clean_text = keep_body_sections(raw_text)
    return FulltextExtraction(
        raw_text=raw_text,
        clean_text=clean_text,
        extraction_method=method,
        clean_strategy="body_sections_v1",
        mojibake_hits=count_mojibake_hits(clean_text),
    )


def _extract_pdf_text_pymupdf(data: bytes) -> str:
    try:
        import fitz
    except Exception:
        return ""
    try:
        parts: list[str] = []
        with fitz.open(stream=data, filetype="pdf") as doc:
            for page in doc:
                text = page.get_text("text")
                if text:
                    parts.append(text)
        return "\n".join(parts)
    except Exception:
        return ""


def keep_body_sections(text: str) -> str:
    start = START_HEADING.search(text)
    end = END_HEADING.search(text)
    begin = start.start() if start else 0
    finish = end.start() if end and end.start() > begin else len(text)
    return normalize_pdf_text(text[begin:finish])


def normalize_pdf_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for bad, good in MOJIBAKE_REPLACEMENTS.items():
        text = text.replace(bad, good)
    text = CONTROL_CHARS.sub("", text)
    text = SOFT_HYPHEN_LINEBREAK.sub(r"\1", text)
    text = MANY_SPACES.sub(" ", text)
    text = MANY_BLANK_LINES.sub("\n\n", text)
    return text.strip()


def count_mojibake_hits(text: str) -> int:
    return len(MOJIBAKE_MARKER.findall(text))


def upsert_paper_fulltext(
    paper_id: str,
    *,
    minio_pdf_key: str | None,
    raw_text: str,
    clean_text: str,
    extraction_method: str,
    clean_strategy: str,
    mojibake_hits: int,
) -> None:
    now = utc_now()
    with get_db() as db:
        db.execute(
            """
            INSERT INTO paper_fulltexts(
                paper_id,
                minio_pdf_key,
                extraction_method,
                clean_strategy,
                raw_text,
                clean_text,
                raw_chars,
                clean_chars,
                mojibake_hits,
                parse_finish_time,
                upload_time,
                update_time
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(paper_id) DO UPDATE SET
                minio_pdf_key = excluded.minio_pdf_key,
                extraction_method = excluded.extraction_method,
                clean_strategy = excluded.clean_strategy,
                raw_text = excluded.raw_text,
                clean_text = excluded.clean_text,
                raw_chars = excluded.raw_chars,
                clean_chars = excluded.clean_chars,
                mojibake_hits = excluded.mojibake_hits,
                parse_finish_time = excluded.parse_finish_time,
                update_time = excluded.update_time
            """,
            (
                paper_id,
                minio_pdf_key,
                extraction_method,
                clean_strategy,
                raw_text,
                clean_text,
                len(raw_text),
                len(clean_text),
                mojibake_hits,
                now,
                now,
                now,
            ),
        )
        db.execute(
            """
            UPDATE papers
            SET parse_finish_time = ?,
                upload_time = COALESCE(upload_time, created_at, ?),
                last_refresh_time = ?,
                update_time = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (now, now, now, now, now, paper_id),
        )


def normalize_chunk_strategy(strategy: str | None) -> str:
    candidate = (strategy or "fixed_1024_200").strip()
    aliases = {
        "fixed_1024_200": "fixed_boundary_v1",
        "fixed": "fixed_boundary_v1",
        "fixed_boundary": "fixed_boundary_v1",
        "paragraph": "paragraph_sentence_v1",
        "paragraph_sentence": "paragraph_sentence_v1",
        "section": "section_parent_child_v1",
        "section_parent_child": "section_parent_child_v1",
        "semantic": "semantic_bge_m3_v1",
        "semantic_bge_m3": "semantic_bge_m3_v1",
        "semantic_chunk": "semantic_bge_m3_v1",
    }
    if candidate in aliases:
        return aliases[candidate]
    if candidate in SUPPORTED_STRATEGIES:
        return candidate
    return candidate


def local_chunk_text(text: str, size: int = 1024, overlap: int = 200) -> list[str]:
    text = " ".join((text or "").split())
    if not text:
        return []
    chunks: list[str] = []
    step = max(size - overlap, 1)
    for start in range(0, len(text), step):
        chunk = text[start : start + size].strip()
        if chunk:
            chunks.append(chunk)
        if start + size >= len(text):
            break
    return chunks


def chunk_text(text: str, size: int = 1024, overlap: int = 200, strategy: str | None = None, paper_id: str | None = None) -> list[str]:
    normalized = normalize_chunk_strategy(strategy)
    if normalized in {"paragraph_sentence_v1", "section_parent_child_v1"}:
        records = build_local_chunk_records(text, str(paper_id or "local-paper"), normalized)
        return [record["content"] for record in records]
    if normalized == "semantic_bge_m3_v1":
        records, _ = chunk_records_via_semantic_chunker(text)
        return [record["content"] for record in records]
    if normalized == "fixed_boundary_v1":
        return local_chunk_text(text, size=size, overlap=overlap)
    raise ValueError(f"unsupported local chunk strategy: {normalized}")


def chunk_records_via_semantic_chunker(text: str) -> tuple[list[dict[str, Any]], list[Any]]:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from chunker import chunk_semantic_details
        fallback_reason = None
    except Exception as exc:
        return _semantic_fallback_records(text, f"semantic splitter is not available: {exc}")

    try:
        details = chunk_semantic_details(text, chunk_size=1024, overlap=200)
    except Exception as exc:
        return _semantic_fallback_records(text, f"semantic splitter failed: {exc}")

    records: list[dict[str, Any]] = []
    for index, item in enumerate(details):
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        records.append(
            {
                "content": content,
                "chunk_index": index,
                "section_path": [],
                "parent_chunk_id": None,
                "splitter": item.get("splitter"),
                "cut_method": item.get("cut_method"),
            }
        )
    warnings: list[Any] = []
    if fallback_reason:
        warnings.append(
            {
                "code": "SEMANTIC_MODEL_UNAVAILABLE",
                "message": "Semantic model dependency is unavailable; paragraph-boundary fallback chunks were used.",
                "details": {"reason": fallback_reason},
            }
        )
    return records, warnings


def _semantic_fallback_records(text: str, reason: str) -> tuple[list[dict[str, Any]], list[Any]]:
    records = build_local_chunk_records(text, "semantic-local-fallback", "paragraph_sentence_v1")
    for index, record in enumerate(records):
        record["chunk_index"] = index
        record["parent_chunk_id"] = None
        record["cut_method"] = "semantic_bge_m3_v1_fallback_paragraph_sentence_v1"
    return records, [
        {
            "code": "SEMANTIC_MODEL_UNAVAILABLE",
            "message": "Semantic model dependency is unavailable; paragraph-boundary fallback chunks were used.",
            "details": {"reason": reason},
        }
    ]


def replace_chunks(paper_id: str, text: str, *, strategy: str | None = None) -> tuple[int, list[Any]]:
    normalized_strategy = normalize_chunk_strategy(strategy)
    warnings: list[Any] = []
    if normalized_strategy == "fixed_boundary_v1":
        chunks = [{"content": value, "section_path": [], "parent_chunk_id": None} for value in local_chunk_text(text)]
    elif normalized_strategy == "semantic_bge_m3_v1":
        chunks, warnings = chunk_records_via_semantic_chunker(text)
    elif normalized_strategy in {"paragraph_sentence_v1", "section_parent_child_v1"}:
        chunks = build_local_chunk_records(text, paper_id, normalized_strategy)
    else:
        raise ValueError(f"unsupported local chunk strategy: {normalized_strategy}")
    now = utc_now()
    chunk_rows: list[dict] = []
    with get_db() as db:
        db.execute("DELETE FROM paper_chunks WHERE paper_id = ?", (paper_id,))
        for index, chunk in enumerate(chunks):
            chunk_id = f"{paper_id}_chunk_{index}"
            content = chunk["content"]
            section_path = chunk.get("section_path") or []
            parent_chunk_id = chunk.get("parent_chunk_id")
            chunk_rows.append(
                {
                    "chunk_id": chunk_id,
                    "paper_id": paper_id,
                    "chunk_index": index,
                    "content": content,
                    "page_no": None,
                }
            )
            db.execute(
                """
                INSERT INTO paper_chunks(
                    chunk_id,
                    paper_id,
                    chunk_index,
                    content,
                    page_no,
                    vector_key,
                    section_path,
                    parent_chunk_id,
                    splitter,
                    cut_method,
                    chunk_create_time,
                    chunk_update_time,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    paper_id,
                    index,
                    content,
                    None,
                    f"local:{chunk_id}",
                    json.dumps(section_path, ensure_ascii=False),
                    parent_chunk_id,
                    normalized_strategy,
                    chunk.get("cut_method") or normalized_strategy,
                    now,
                    now,
                    now,
                ),
            )
        db.execute(
            """
            UPDATE papers
            SET parse_status = ?,
                parse_error = ?,
                chunk_gen_time = ?,
                vector_index_time = ?,
                last_refresh_time = ?,
                update_time = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (2 if chunks else -1, None if chunks else "paper has no chunkable text", now, None, now, now, now, paper_id),
        )
    delete_chunks_milvus(paper_id)
    vectorized = index_chunks_milvus(paper_id, chunk_rows)
    if vectorized:
        with get_db() as db:
            indexed_at = utc_now()
            db.execute(
                """
                UPDATE papers
                SET parse_status = ?,
                    vector_index_time = ?,
                    last_refresh_time = ?,
                    update_time = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (3, indexed_at, indexed_at, indexed_at, indexed_at, paper_id),
            )
    return len(chunks), warnings


def index_pdf_file(paper_id: str, path: Path) -> int:
    data = path.read_bytes()
    text = extract_text_from_pdf_bytes(data)
    return replace_chunks(paper_id, text)[0]
