from __future__ import annotations

import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from flask import Blueprint, current_app, request, send_file

from .responses import error


bp = Blueprint("academic_daily", __name__)
ALLOWED_PDF_HOSTS = {"arxiv.org", "export.arxiv.org", "cn.arxiv.org", "xxx.itp.ac.cn"}
ARXIV_ID_PATTERN = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$", re.IGNORECASE)


def is_allowed_arxiv_pdf(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    article_id = parsed.path.removeprefix("/pdf/").removesuffix(".pdf")
    return (
        parsed.scheme in {"http", "https"}
        and host in ALLOWED_PDF_HOSTS
        and parsed.path.startswith("/pdf/")
        and bool(ARXIV_ID_PATTERN.fullmatch(article_id))
        and not parsed.username
        and not parsed.password
    )


def arxiv_article_id(source: str) -> str:
    if not is_allowed_arxiv_pdf(source):
        return ""
    return urlparse(source).path.removeprefix("/pdf/").removesuffix(".pdf")


def candidate_pdf_urls(source: str) -> list[str]:
    article_id = arxiv_article_id(source)
    if not article_id:
        return []
    candidates = [source]
    for host in ("arxiv.org", "export.arxiv.org", "cn.arxiv.org"):
        candidates.append(f"https://{host}/pdf/{article_id}")
    candidates.append(f"http://xxx.itp.ac.cn/pdf/{article_id}")
    return list(dict.fromkeys(candidates))


@bp.get("/academic-daily/pdf")
def academic_daily_pdf():
    source = str(request.args.get("source") or "").strip()
    if not is_allowed_arxiv_pdf(source):
        return error("仅允许读取 arXiv 原版 PDF 地址", code="ARXIV_PDF_SOURCE_INVALID", status=400)
    local_pdf = download_original_pdf(source)
    if local_pdf is None:
        return error(
            "arXiv 当前未提供该条目的原版 PDF，系统不会使用 HTML 伪造 PDF。",
            code="ARXIV_PDF_NOT_AVAILABLE",
            status=404,
        )
    response = send_file(
        local_pdf,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=local_pdf.name,
        max_age=0,
    )
    response.headers["Content-Disposition"] = f'inline; filename="{local_pdf.name}"'
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["X-Pdf-Source"] = "arxiv-original-temporary-cache"
    return response


def download_original_pdf(source: str) -> Path | None:
    article_id = arxiv_article_id(source)
    if not article_id:
        return None
    cache_root = Path(current_app.config["ARXIV_DAILY_PDF_CACHE_DIR"]).resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    safe_name = article_id.replace("/", "_")
    cached = (cache_root / f"{safe_name}.pdf").resolve()
    if not cached.is_relative_to(cache_root):
        return None
    if valid_cached_pdf(cached):
        return cached

    max_bytes = int(current_app.config["ARXIV_DAILY_PDF_MAX_BYTES"])
    for candidate in candidate_pdf_urls(source):
        temporary_path: Path | None = None
        try:
            with httpx.stream(
                "GET",
                candidate,
                follow_redirects=True,
                timeout=45.0,
                headers={"User-Agent": "ZhiyanArxivDaily/1.0 (+original PDF cache)"},
            ) as response:
                if response.status_code != 200 or not is_allowed_arxiv_pdf(str(response.url)):
                    continue
                content_type = response.headers.get("content-type", "").lower()
                if content_type and not any(
                    item in content_type for item in ("application/pdf", "application/octet-stream")
                ):
                    continue
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=f"{safe_name}-",
                    suffix=".part",
                    dir=cache_root,
                    delete=False,
                ) as temporary_file:
                    temporary_path = Path(temporary_file.name)
                    received = 0
                    for chunk in response.iter_bytes():
                        received += len(chunk)
                        if received > max_bytes:
                            raise ValueError("PDF exceeds configured size limit")
                        temporary_file.write(chunk)
            if temporary_path is not None and valid_cached_pdf(temporary_path):
                temporary_path.replace(cached)
                return cached
        except (httpx.HTTPError, OSError, ValueError):
            pass
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
    return None


def valid_cached_pdf(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 1024:
            return False
        with path.open("rb") as cached_file:
            return cached_file.read(5) == b"%PDF-"
    except OSError:
        return False
