"""Background worker: chunk and vectorise knowledge-base papers one by one.

Runs next to the web backend (which owns the single-process Milvus Lite
store) and feeds it one paper at a time through
POST /api/v1/knowledge-base/admin/papers/<id>/index-text.

Normally installed as the systemd service ``zhiyan-kb-index`` (see
deployment notes), so it keeps running without anyone logged in:
    ../.venv/bin/python scripts/kb_index_daemon.py --continuous --self-download \
        --text-dir /www/wwwroot/zhiyan-data/kb-texts \
        --milvus-dir /www/wwwroot/zhiyan-data/milvus --max-milvus-gb 4.5 --min-free-gb 8

Work is taken in batches of --limit not-yet-indexed arXiv papers (admin-list
order), frozen into <text-dir>/_index_targets.json so a restart resumes the
same batch. With --continuous the next batch is built when one finishes,
until no paper is left. Text comes from <text-dir>/<arxiv id>.txt; with
--self-download, missing texts are fetched from arXiv by a few parallel
downloaders that stay only a little ahead of the indexer, and each PDF is
deleted as soon as its text is extracted.

Disk is protected two ways: the worker stops (exit 0, so systemd does not
restart it) when free space falls below --min-free-gb or the Milvus store
grows past --max-milvus-gb. It waits while memory is low and retries while
the backend restarts. Papers that keep failing (e.g. withdrawn from arXiv)
are remembered in <text-dir>/_index_state.json and skipped. Create
<text-dir>/_index_stop to stop it cleanly.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")
TARGETS_NAME = "_index_targets.json"
STATE_NAME = "_index_state.json"
STOP_NAME = "_index_stop"


def log(message: str) -> None:
    print(time.strftime("%m-%d %H:%M:%S"), message, flush=True)


def read_env() -> dict[str, str]:
    values: dict[str, str] = dict()
    for line in (BACKEND / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def connect(env: dict[str, str]):
    import psycopg

    url = env["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    return psycopg.connect(url, autocommit=True)


def arxiv_id(*values: object) -> str | None:
    for value in values:
        match = ARXIV_ID.search(str(value or ""))
        if match:
            return match.group(1)
    return None


def build_targets(conn, schema: str, limit: int) -> list[dict]:
    rows = conn.execute(
        f'SELECT id, pdf_url, paper_url FROM "{schema}".papers '
        "WHERE delete_time IS NULL AND vector_index_time IS NULL ORDER BY updated_at DESC, id"
    ).fetchall()
    targets: list[dict] = []
    seen: set[str] = set()
    for paper_id, pdf_url, paper_url in rows:
        aid = arxiv_id(pdf_url, paper_url, paper_id)
        if not aid or aid in seen:
            continue
        seen.add(aid)
        targets.append(dict(id=paper_id, arxiv_id=aid))
        if len(targets) >= limit:
            break
    return targets


def indexed_ids(conn, schema: str, ids: list[str]) -> set[str]:
    rows = conn.execute(
        f'SELECT id FROM "{schema}".papers WHERE vector_index_time IS NOT NULL AND id = ANY(%s)',
        (ids,),
    ).fetchall()
    return set(row[0] for row in rows)


def admin_token(conn, env: dict[str, str]) -> str:
    """Sign a session token for a system administrator (same format as login)."""
    from itsdangerous import URLSafeTimedSerializer

    schema = env.get("KB_SHARED_USER_SCHEMA", "zhiyan")
    row = conn.execute(
        f'SELECT id, session_version FROM "{schema}".users '
        "WHERE role_code = 'system_admin' AND status = 'ACTIVE' AND deleted_at IS NULL "
        "ORDER BY created_at LIMIT 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("no active system_admin user")
    serializer = URLSafeTimedSerializer(env["SECRET_KEY"], salt="zhiyan-auth-v1")
    return serializer.dumps(dict(user_id=str(row[0]), session_version=row[1], csrf="kb-index-daemon"))


def mem_available_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0


ARXIV_PDF_HOSTS = ("arxiv.org", "export.arxiv.org")


def download_text(aid: str, text_dir: Path, timeout: int) -> Path:
    """Fetch one PDF from arXiv (falling back to its export mirror) and keep only its page text."""
    last_error: Exception | None = None
    for host in ARXIV_PDF_HOSTS:
        try:
            return _download_text_from(host, aid, text_dir, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise
            last_error = exc
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise last_error or RuntimeError("download failed")


def _download_text_from(host: str, aid: str, text_dir: Path, timeout: int) -> Path:
    import fitz

    pdf_path = text_dir / f"_{aid}.{threading.get_ident()}.pdf.part"
    request = urllib.request.Request(
        f"https://{host}/pdf/{aid}", headers={"User-Agent": "Zhiyan-KnowledgeBase/1.0"}
    )
    deadline = time.monotonic() + timeout
    try:
        with urllib.request.urlopen(request, timeout=60) as response, pdf_path.open("wb") as target:
            while True:
                block = response.read(64 * 1024)
                if not block:
                    break
                target.write(block)
                if time.monotonic() > deadline:
                    raise TimeoutError(f"download exceeded {timeout}s")
                if target.tell() > 60 * 1024 * 1024:
                    raise ValueError("PDF larger than 60 MB")
        parts: list[str] = []
        with fitz.open(str(pdf_path)) as doc:
            for page in doc:
                text = page.get_text("text")
                if text:
                    parts.append(text)
    finally:
        pdf_path.unlink(missing_ok=True)
    text = "\n".join(parts)
    if len(text.strip()) < 200:
        raise ValueError("no extractable text")
    text_path = text_dir / f"{aid}.txt"
    partial = text_dir / f"_{aid}.txt.part"
    partial.write_text(text, encoding="utf-8")
    partial.replace(text_path)
    return text_path


def dir_size_gb(path: Path) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total / 1024 ** 3


class Downloader:
    """A few threads that fetch texts for the papers the indexer will reach next.

    They never run more than ``ahead`` papers in front of the indexer, so the
    text cache grows only as fast as papers are indexed.
    """

    def __init__(self, text_dir: Path, workers: int, ahead: int, timeout: int, attempts: dict[str, int], save_state) -> None:
        self.text_dir = text_dir
        self.ahead = ahead
        self.timeout = timeout
        self.attempts = attempts
        self.save_state = save_state
        self.lock = threading.Lock()
        self.queue: list[dict] = []
        self.busy: set[str] = set()
        self.stopped = False
        for index in range(workers):
            threading.Thread(target=self._work, name=f"kb-download-{index}", daemon=True).start()

    def want(self, pending: list[dict]) -> None:
        """The next papers without text, in indexing order."""
        missing = [item for item in pending if not (self.text_dir / f"{item['arxiv_id']}.txt").is_file()]
        with self.lock:
            self.queue = missing[: self.ahead]

    def _take(self) -> dict | None:
        with self.lock:
            for item in self.queue:
                if item["id"] not in self.busy:
                    self.busy.add(item["id"])
                    return item
        return None

    def _work(self) -> None:
        while not self.stopped:
            paper = self._take()
            if paper is None:
                time.sleep(5)
                continue
            try:
                started = time.monotonic()
                download_text(paper["arxiv_id"], self.text_dir, self.timeout)
                log(f"downloaded {paper['arxiv_id']} in {time.monotonic() - started:.0f}s")
            except Exception as exc:  # noqa: BLE001
                with self.lock:
                    # A paper arXiv no longer has will not come back; give up at once.
                    gone = isinstance(exc, urllib.error.HTTPError) and exc.code == 404
                    self.attempts[paper["id"]] = self.attempts.get(paper["id"], 0) + (99 if gone else 1)
                    self.save_state()
                log(f"download failed {paper['arxiv_id']}: {type(exc).__name__}: {str(exc)[:120]}")
                time.sleep(30)
            finally:
                with self.lock:
                    self.busy.discard(paper["id"])
                    self.queue = [item for item in self.queue if item["id"] != paper["id"]]
            time.sleep(3)  # be polite to arXiv between requests


def post_text(base_url: str, token: str, paper_id: str, text: str, timeout: int) -> dict:
    body = json.dumps(dict(text=text)).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/v1/knowledge-base/admin/papers/{urllib.request.quote(paper_id, safe='')}/index-text",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Chunk and vectorise knowledge-base papers in the background")
    parser.add_argument("--limit", type=int, default=2000, help="papers per frozen target batch")
    parser.add_argument("--continuous", action="store_true", help="start the next batch when one finishes")
    parser.add_argument("--text-dir", type=Path, required=True)
    parser.add_argument("--milvus-dir", type=Path, default=None, help="Milvus Lite store, for --max-milvus-gb")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--self-download", action="store_true", help="fetch PDFs from arXiv when no text file exists")
    parser.add_argument("--download-workers", type=int, default=3)
    parser.add_argument("--download-ahead", type=int, default=8, help="papers to prefetch in front of the indexer")
    parser.add_argument("--download-timeout", type=int, default=1500)
    parser.add_argument("--min-free-gb", type=float, default=8.0)
    parser.add_argument("--max-milvus-gb", type=float, default=0.0, help="stop when the Milvus store exceeds this (0 = no cap)")
    parser.add_argument("--min-mem-mb", type=int, default=450)
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args()

    env = read_env()
    schema = env.get("KB_DB_SCHEMA", "knowledge_base")
    text_dir: Path = args.text_dir
    text_dir.mkdir(parents=True, exist_ok=True)
    targets_path = text_dir / TARGETS_NAME
    state_path = text_dir / STATE_NAME
    conn = connect(env)

    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else dict(attempts=dict())
    attempts: dict[str, int] = state.setdefault("attempts", dict())

    def save_state() -> None:
        state_path.write_text(json.dumps(state), encoding="utf-8")

    def new_batch() -> list[dict]:
        candidates = build_targets(conn, schema, args.limit + len(attempts))
        batch = [item for item in candidates if attempts.get(item["id"], 0) < args.max_attempts][: args.limit]
        targets_path.write_text(json.dumps(batch), encoding="utf-8")
        return batch

    targets = json.loads(targets_path.read_text(encoding="utf-8")) if targets_path.is_file() else new_batch()
    log(f"batch of {len(targets)} papers; text dir {text_dir}")

    downloader = (
        Downloader(text_dir, args.download_workers, args.download_ahead, args.download_timeout, attempts, save_state)
        if args.self_download
        else None
    )
    state_lock = downloader.lock if downloader else threading.Lock()
    token = admin_token(conn, env)
    last_space_check = 0.0
    while True:
        if (text_dir / STOP_NAME).exists():
            log("stop file present; exiting")
            return 0
        if time.monotonic() - last_space_check > 60:
            last_space_check = time.monotonic()
            free_gb = shutil.disk_usage(text_dir).free / 1024 ** 3
            if free_gb < args.min_free_gb:
                log(f"free disk {free_gb:.1f} GB below floor {args.min_free_gb} GB; stopping")
                return 0
            if args.max_milvus_gb and args.milvus_dir:
                milvus_gb = dir_size_gb(args.milvus_dir)
                if milvus_gb > args.max_milvus_gb:
                    log(f"Milvus store {milvus_gb:.2f} GB above cap {args.max_milvus_gb} GB; stopping")
                    return 0
        if mem_available_mb() < args.min_mem_mb:
            log(f"available memory {mem_available_mb()} MB below floor; waiting 5 min")
            time.sleep(300)
            continue

        done = indexed_ids(conn, schema, [item["id"] for item in targets])
        with state_lock:
            pending = [
                item for item in targets
                if item["id"] not in done and attempts.get(item["id"], 0) < args.max_attempts
            ]
        if not pending:
            failed = sum(1 for item in targets if item["id"] not in done)
            log(f"batch finished: {len(done)} indexed, {failed} gave up")
            if not args.continuous:
                return 0
            targets = new_batch()
            if not targets:
                log("no papers left to index; exiting")
                return 0
            log(f"next batch of {len(targets)} papers")
            continue

        if downloader:
            downloader.want(pending)
        paper = next((item for item in pending if (text_dir / f"{item['arxiv_id']}.txt").is_file()), None)
        if paper is None:
            time.sleep(5)
            continue

        text = (text_dir / f"{paper['arxiv_id']}.txt").read_text(encoding="utf-8", errors="replace")
        try:
            result = post_text(args.base_url, token, paper["id"], text, timeout=900)
            if not result.get("vectorized"):
                raise RuntimeError(f"chunks={result.get('chunks')} but vectors were not written")
            log(f"[{len(done) + 1}/{len(targets)}] {paper['arxiv_id']} {result.get('chunks')} chunks")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:160]
            if exc.code in (401, 403):
                token = admin_token(conn, env)
                time.sleep(5)
                continue
            with state_lock:
                attempts[paper["id"]] = attempts.get(paper["id"], 0) + 1
                save_state()
            log(f"index failed {paper['arxiv_id']}: HTTP {exc.code} {detail}")
            time.sleep(10)
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            log(f"backend not reachable ({type(exc).__name__}); retrying in 20s")
            time.sleep(20)
        except Exception as exc:  # noqa: BLE001
            with state_lock:
                attempts[paper["id"]] = attempts.get(paper["id"], 0) + 1
                save_state()
            log(f"index failed {paper['arxiv_id']}: {type(exc).__name__}: {str(exc)[:160]}")
            time.sleep(10)
        time.sleep(0.5)

if __name__ == "__main__":
    raise SystemExit(main())
