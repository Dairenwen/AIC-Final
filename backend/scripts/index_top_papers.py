"""Chunk and vectorise the papers at the top of the knowledge-base list.

Usage (from backend/):
    ../.venv/bin/python scripts/index_top_papers.py --limit 150 --pdf-dir /path/to/pdfs

Papers are taken in the order the knowledge-base admin list shows them
(most recently updated first). For each paper the text is taken from
--text-dir (``<arxiv id>.txt``, raw page text extracted elsewhere), or the
PDF is read from --pdf-dir (``<arxiv id>.pdf``) or downloaded from arXiv.
The text is cleaned and split with the platform's chunking code, embedded
and written to Milvus.
PDFs are deleted after indexing unless --keep-pdfs is given, so the disk
only ever holds the vectors and chunk text.
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

from app import create_app  # noqa: E402

ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")


def arxiv_id(paper: dict) -> str | None:
    for value in (paper.get("pdf_url"), paper.get("paper_url"), paper.get("id")):
        match = ARXIV_ID.search(str(value or ""))
        if match:
            return match.group(1)
    return None


def candidate_papers(db, limit: int, include_indexed: bool) -> list[dict]:
    rows = db.execute(
        """
        SELECT id, title, pdf_url, paper_url, vector_index_time
        FROM papers
        WHERE delete_time IS NULL
        ORDER BY updated_at DESC, id
        """
    ).fetchall()
    picked = []
    for row in rows:
        paper = dict(row)
        if not arxiv_id(paper):
            continue
        if paper.get("vector_index_time") and not include_indexed:
            picked.append({**paper, "skip": True})
        else:
            picked.append(paper)
        if len(picked) >= limit:
            break
    return picked


def fetch_pdf(paper: dict, pdf_dir: Path | None, work_dir: Path, last_download: list[float]) -> tuple[Path, bool]:
    """Return (path, downloaded). Local copies are used when available."""
    aid = arxiv_id(paper)
    if pdf_dir is not None:
        local = pdf_dir / f"{aid}.pdf"
        if local.is_file() and local.stat().st_size > 1024:
            return local, False
    url = f"https://arxiv.org/pdf/{aid}"
    wait = 3.0 - (time.monotonic() - last_download[0])
    if wait > 0:
        time.sleep(wait)
    request = urllib.request.Request(url, headers={"User-Agent": "Zhiyan-KnowledgeBase/1.0"})
    target = work_dir / f"{aid}.pdf"
    with urllib.request.urlopen(request, timeout=60) as response:
        target.write_bytes(response.read())
    last_download[0] = time.monotonic()
    return target, True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=150, help="number of papers from the top of the list")
    parser.add_argument("--pdf-dir", type=Path, default=None, help="directory with <arxiv id>.pdf files")
    parser.add_argument("--text-dir", type=Path, default=None, help="directory with <arxiv id>.txt raw page text")
    parser.add_argument("--keep-pdfs", action="store_true", help="do not delete PDFs after indexing")
    parser.add_argument("--reindex", action="store_true", help="also re-index papers that already have vectors")
    parser.add_argument("--embed-existing", action="store_true", help="also vectorise chunks that already exist for other papers")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        from knowledge_base_runtime.backend.config.settings import KB_MILVUS_ENABLED
        from knowledge_base_runtime.backend.dao.database import get_db
        from knowledge_base_runtime.backend.service.chunking import (
            index_pdf_file,
            keep_body_sections,
            normalize_pdf_text,
            replace_chunks,
        )

        if not KB_MILVUS_ENABLED:
            print("KB_MILVUS_ENABLED is off; enable it in backend/.env first.", file=sys.stderr)
            return 2
        with get_db() as db:
            papers = candidate_papers(db, args.limit, args.reindex)
        print(f"{len(papers)} papers selected ({sum(1 for p in papers if p.get('skip'))} already indexed)", flush=True)

        done = failed = chunks_total = 0
        last_download = [0.0]
        with tempfile.TemporaryDirectory(prefix="zy-index-") as tmp:
            work_dir = Path(tmp)
            for position, paper in enumerate(papers, start=1):
                if paper.get("skip"):
                    continue
                label = f"[{position}/{len(papers)}] {paper['id']}"
                try:
                    text_file = args.text_dir / f"{arxiv_id(paper)}.txt" if args.text_dir else None
                    if text_file is not None and text_file.is_file():
                        # Same cleaning that index_pdf_file applies to extracted PDF text.
                        text = keep_body_sections(normalize_pdf_text(text_file.read_text(encoding="utf-8")))
                        count = replace_chunks(paper["id"], text)[0]
                        path, downloaded = text_file, False
                    else:
                        path, downloaded = fetch_pdf(paper, args.pdf_dir, work_dir, last_download)
                        count = index_pdf_file(paper["id"], path)
                    with get_db() as db:
                        row = db.execute("SELECT vector_index_time FROM papers WHERE id = ?", (paper["id"],)).fetchone()
                    if count and not (row and row["vector_index_time"]):
                        raise RuntimeError("chunks saved but vectors were not written; check the embedding API and Milvus")
                    chunks_total += count
                    done += 1
                    print(f"{label} {count} chunks  {str(paper.get('title') or '')[:60]}", flush=True)
                    if downloaded or (path.suffix == ".pdf" and not args.keep_pdfs):
                        path.unlink(missing_ok=True)
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    print(f"{label} FAILED: {type(exc).__name__}: {str(exc)[:160]}", flush=True)

        if args.embed_existing:
            # Papers chunked earlier (e.g. from the admin page) only need their
            # existing chunks embedded; their text is not re-extracted.
            from knowledge_base_runtime.backend.client.retrieval_backends import index_chunks_milvus

            handled = {paper["id"] for paper in papers if not paper.get("skip")}
            with get_db() as db:
                rows = db.execute(
                    "SELECT paper_id, chunk_id, chunk_index, content, page_no FROM paper_chunks "
                    "WHERE chunk_expire_time IS NULL ORDER BY paper_id, chunk_index"
                ).fetchall()
            grouped: dict[str, list[dict]] = {}
            for row in rows:
                if row["paper_id"] not in handled:
                    grouped.setdefault(row["paper_id"], []).append(dict(row))
            for paper_id, chunk_rows in grouped.items():
                ok = index_chunks_milvus(paper_id, chunk_rows)
                if ok:
                    with get_db() as db:
                        db.execute(
                            "UPDATE papers SET vector_index_time = COALESCE(vector_index_time, ?) WHERE id = ?",
                            (time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), paper_id),
                        )
                print(f"[existing] {paper_id} {len(chunk_rows)} chunks {'ok' if ok else 'FAILED'}", flush=True)

        with get_db() as db:
            indexed = db.execute(
                "SELECT COUNT(*) AS c FROM papers WHERE vector_index_time IS NOT NULL AND delete_time IS NULL"
            ).fetchone()["c"]
        print(f"done: {done} indexed, {failed} failed, {chunks_total} chunks; papers with vectors now: {indexed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
