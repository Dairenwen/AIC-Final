"""Make PDFs uploaded to 我的知识库 searchable by 知识库问答.

Platform papers are chunked and vectorised by the knowledge-base runtime.
Local uploads never were, so they could not be found.  This module chunks
them page by page (keeping page numbers for citations) into
``zhiyan.personal_knowledge_chunks`` and stores an embedding per chunk when
the platform embedding service is available.  Indexing runs in a background
thread after the upload is saved, and its state is kept in the paper's
metadata (``index_status``: INDEXING / INDEXED / FAILED).
"""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any
from uuid import UUID

from flask import Flask, current_app
from sqlalchemy import delete, select

from ..extensions import db
from ..models import PersonalKnowledgeChunk, PersonalKnowledgePaper

CHUNK_SIZE = 900
CHUNK_OVERLAP = 150
MIN_CHUNK_CHARS = 40
EMBED_BATCH = 16


def schedule_personal_index(app: Flask, paper_id: UUID) -> None:
    thread = threading.Thread(
        target=_index_with_context,
        args=(app, paper_id),
        name=f"personal-kb-index-{paper_id}",
        daemon=True,
    )
    thread.start()


def _index_with_context(app: Flask, paper_id: UUID) -> None:
    with app.app_context():
        try:
            index_personal_paper(paper_id)
        except Exception:  # noqa: BLE001 - the status records the failure
            db.session.rollback()
            app.logger.warning("personal knowledge indexing failed for %s", paper_id, exc_info=True)
            _set_status(paper_id, "FAILED")
        finally:
            db.session.remove()


def index_personal_paper(paper_id: UUID) -> int:
    """Chunk (and embed) one uploaded PDF; returns the number of chunks."""
    paper = db.session.get(PersonalKnowledgePaper, paper_id)
    if paper is None or paper.source_type != "LOCAL_UPLOAD" or not paper.object_key:
        return 0
    path = Path(current_app.config["PERSONAL_KB_UPLOAD_DIR"]) / paper.object_key
    pages = pdf_pages(path.read_bytes())
    records = [
        {"page_no": page_no, "content": chunk}
        for page_no, text in pages
        for chunk in split_page(text)
    ]
    vectors = embed_chunks([item["content"] for item in records])

    db.session.execute(delete(PersonalKnowledgeChunk).where(PersonalKnowledgeChunk.paper_id == paper.id))
    for index, item in enumerate(records):
        db.session.add(
            PersonalKnowledgeChunk(
                paper_id=paper.id,
                owner_user_id=paper.owner_user_id,
                chunk_index=index,
                page_no=item["page_no"],
                content=item["content"],
                embedding=vectors[index] if vectors else None,
            )
        )
    paper.metadata_json = {
        **(paper.metadata_json or {}),
        "index_status": "INDEXED" if records else "FAILED",
        "index_chunks": len(records),
        "index_semantic": bool(vectors),
        "index_error": None if records else "没有从 PDF 里读到文字",
    }
    db.session.commit()
    return len(records)


def pdf_pages(data: bytes) -> list[tuple[int, str]]:
    import fitz

    from knowledge_base_runtime.backend.service.chunking import normalize_pdf_text

    pages: list[tuple[int, str]] = []
    with fitz.open(stream=data, filetype="pdf") as document:
        for number, page in enumerate(document, 1):
            text = normalize_pdf_text(page.get_text("text") or "")
            if text.strip():
                pages.append((number, text))
    return pages


def split_page(text: str) -> list[str]:
    from knowledge_base_runtime.backend.service.chunking import local_chunk_text

    return [
        chunk
        for chunk in local_chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
        if len(chunk) >= MIN_CHUNK_CHARS
    ]


def embed_chunks(texts: list[str]) -> list[list[float]] | None:
    """Embeddings for all chunks, or None when semantic search is off/unavailable."""
    if not texts or not current_app.config.get("KNOWLEDGE_BASE_MILVUS_ENABLED"):
        return None
    try:
        from knowledge_base_runtime.backend.service.retrieval_backends import embed_texts

        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            vectors.extend(embed_texts(texts[start : start + EMBED_BATCH]))
    except Exception:  # noqa: BLE001 - fall back to keyword search only
        current_app.logger.warning("personal knowledge embedding unavailable", exc_info=True)
        return None
    return vectors if len(vectors) == len(texts) else None


def embed_query(question: str) -> list[float] | None:
    vectors = embed_chunks([question])
    return vectors[0] if vectors else None


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


def mark_indexing(paper: PersonalKnowledgePaper) -> None:
    paper.metadata_json = {**(paper.metadata_json or {}), "index_status": "INDEXING"}


def _set_status(paper_id: UUID, status: str) -> None:
    paper = db.session.get(PersonalKnowledgePaper, paper_id)
    if paper is None:
        return
    paper.metadata_json = {**(paper.metadata_json or {}), "index_status": status}
    db.session.commit()


# ---- Read side, used by 知识库问答 -----------------------------------------


def personal_platform_paper_ids(user_id: str) -> set[str]:
    """Platform papers the person has added to any of their knowledge bases."""
    rows = db.session.scalars(
        select(PersonalKnowledgePaper.platform_paper_id).where(
            PersonalKnowledgePaper.owner_user_id == _uuid(user_id),
            PersonalKnowledgePaper.status == "ACTIVE",
            PersonalKnowledgePaper.platform_paper_id.is_not(None),
        )
    ).all()
    return {str(value) for value in rows if value}


def personal_document_ids(user_id: str) -> set[str]:
    """Ids ("personal:<uuid>") of the person's uploads that have chunks."""
    rows = db.session.scalars(
        select(PersonalKnowledgeChunk.paper_id)
        .join(PersonalKnowledgePaper, PersonalKnowledgePaper.id == PersonalKnowledgeChunk.paper_id)
        .where(
            PersonalKnowledgeChunk.owner_user_id == _uuid(user_id),
            PersonalKnowledgePaper.status == "ACTIVE",
        )
        .distinct()
    ).all()
    return {f"personal:{value}" for value in rows}


def personal_chunks(user_id: str) -> list[dict[str, Any]]:
    """All chunks of the person's own uploads, with the paper title."""
    rows = db.session.execute(
        select(PersonalKnowledgeChunk, PersonalKnowledgePaper.title)
        .join(PersonalKnowledgePaper, PersonalKnowledgePaper.id == PersonalKnowledgeChunk.paper_id)
        .where(
            PersonalKnowledgeChunk.owner_user_id == _uuid(user_id),
            PersonalKnowledgePaper.status == "ACTIVE",
        )
        .order_by(PersonalKnowledgeChunk.paper_id, PersonalKnowledgeChunk.chunk_index)
    ).all()
    return [
        {
            "chunk_id": f"personal:{chunk.id}",
            "document_id": f"personal:{chunk.paper_id}",
            "title": title or "未命名文献",
            "content": chunk.content,
            "page_no": chunk.page_no,
            "embedding": chunk.embedding,
        }
        for chunk, title in rows
    ]


def _uuid(value: str) -> UUID | None:
    try:
        return UUID(str(value))
    except ValueError:
        return None
