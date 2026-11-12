from __future__ import annotations

import csv
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup

from ..extensions import db
from ..models import Skill


TEXT_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".sh",
    ".ps1",
    ".bat",
    ".mjs",
    ".cjs",
    ".css",
    ".html",
    ".xml",
    ".tex",
}
DEFAULT_MAX_FILE_BYTES = 1_500_000
DEFAULT_MAX_TOTAL_BYTES = 8_000_000


def load_crawled_skills(path: Path) -> list[dict[str, Any]]:
    """Load the JSON export, with CSV support for the crawler's companion file."""
    raw = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".csv":
        return [dict(row) for row in csv.DictReader(raw.splitlines())]
    payload = json.loads(raw)
    if isinstance(payload, dict):
        payload = payload.get("items", [])
    if not isinstance(payload, list):
        raise ValueError("技能文件必须是 JSON 数组或包含 items 数组的对象")
    return [item for item in payload if isinstance(item, dict)]


def sync_skills(
    path: Path,
    *,
    timeout: float = 20,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> dict[str, int]:
    """Download and upsert the crawled skills into zhiyan.skills."""
    items = load_crawled_skills(path)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "ZhiyanSkillImporter/1.0 (+research skill catalog)",
            "Accept": "application/json,text/html,text/plain,*/*;q=0.8",
        }
    )
    counts = {"total": 0, "created": 0, "updated": 0, "downloaded": 0, "metadata_only": 0, "failed": 0}
    seen: set[str] = set()
    for item in items:
        name = _text(item.get("title") or item.get("name") or item.get("slug")) or "未命名技能"
        source_url = _source_url(item)
        identity = _identity(name, source_url)
        if identity in seen:
            continue
        seen.add(identity)
        counts["total"] += 1

        try:
            content = download_skill_content(
                source_url,
                session=session,
                timeout=timeout,
                max_file_bytes=max_file_bytes,
                max_total_bytes=max_total_bytes,
            )
        except Exception as exc:  # keep one broken source from cancelling the import
            content = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "files": {},
                "full_content": "",
            }

        if content["status"] == "DOWNLOADED":
            counts["downloaded"] += 1
        elif content["status"] == "ERROR":
            counts["failed"] += 1
        else:
            counts["metadata_only"] += 1

        definition = {
            "import_key": identity,
            "source": {
                "site": _text(item.get("source_site")),
                "query": _text(item.get("source_query")),
                "rank": item.get("source_rank"),
                "score": item.get("score"),
                "author": _text(item.get("author")),
                "category": _text(item.get("category")) or "科研",
                "tags": _tags(item.get("tags") or item.get("matched_terms")),
            },
            "source_url": source_url,
            "install_url": _text(item.get("install_url")),
            "repository": _text(item.get("repository")),
            "download": {
                **content,
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
            },
            "original": item,
        }
        description = _text(item.get("description"))
        existing = db.session.scalar(
            db.select(Skill).where(Skill.name == name, Skill.deleted_at.is_(None))
        )
        if existing is None:
            existing = Skill(
                name=name,
                description=description,
                visibility="PUBLIC",
                review_status="APPROVED",
                published_at=datetime.now(timezone.utc),
                definition_json=definition,
            )
            db.session.add(existing)
            counts["created"] += 1
        else:
            existing.description = description or existing.description
            existing.visibility = "PUBLIC"
            existing.review_status = "APPROVED"
            existing.published_at = existing.published_at or datetime.now(timezone.utc)
            existing.version += 1
            existing.definition_json = definition
            counts["updated"] += 1
    db.session.commit()
    return counts


def download_skill_content(
    source_url: str | None,
    *,
    session: requests.Session,
    timeout: float,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> dict[str, Any]:
    if not source_url:
        return {"status": "METADATA_ONLY", "files": {}, "full_content": ""}
    github = _github_reference(source_url)
    if github:
        return _download_github_tree(
            github,
            session=session,
            timeout=timeout,
            max_file_bytes=max_file_bytes,
            max_total_bytes=max_total_bytes,
        )
    response = session.get(source_url, timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "text" not in content_type and "json" not in content_type and "html" not in content_type:
        return {
            "status": "ERROR",
            "error": f"unsupported content type: {content_type or 'unknown'}",
            "files": {},
            "full_content": "",
        }
    text = response.text
    if "html" in content_type or "<html" in text[:500].lower():
        soup = BeautifulSoup(text, "html.parser")
        for node in soup(["script", "style", "noscript"]):
            node.decompose()
        text = soup.get_text("\n", strip=True)
    text = html.unescape(text).strip()
    return {
        "status": "DOWNLOADED",
        "files": {"source": text},
        "full_content": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "file_count": 1,
    }


def _download_github_tree(
    reference: dict[str, str],
    *,
    session: requests.Session,
    timeout: float,
    max_file_bytes: int,
    max_total_bytes: int,
) -> dict[str, Any]:
    owner, repo, ref, subpath = (
        reference["owner"],
        reference["repo"],
        reference["ref"],
        reference["path"],
    )
    api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{quote(ref, safe='')}"
    response = session.get(api_url, params={"recursive": "1"}, timeout=timeout)
    if response.status_code == 404 and ref == "main":
        ref = "master"
        api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{quote(ref, safe='')}"
        response = session.get(api_url, params={"recursive": "1"}, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    tree = payload.get("tree", [])
    prefix = subpath.strip("/")
    files = [
        entry
        for entry in tree
        if entry.get("type") == "blob"
        and _is_under(entry.get("path", ""), prefix)
        and _is_text_path(entry.get("path", ""))
    ]
    files.sort(key=lambda entry: (0 if Path(entry["path"]).name.lower() == "skill.md" else 1, entry["path"]))
    if not files:
        return {"status": "ERROR", "error": "GitHub source contains no readable skill files", "files": {}, "full_content": ""}

    downloaded: dict[str, str] = {}
    total_bytes = 0
    for entry in files[:100]:
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{quote(ref, safe='')}/{quote(entry['path'], safe='/')}"
        file_response = session.get(raw_url, timeout=timeout)
        file_response.raise_for_status()
        data = file_response.content
        if len(data) > max_file_bytes or total_bytes + len(data) > max_total_bytes:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        downloaded[entry["path"]] = text
        total_bytes += len(data)
    if not downloaded:
        return {"status": "ERROR", "error": "GitHub skill files exceeded configured download limits", "files": {}, "full_content": ""}
    full_content = "\n\n".join(f"===== {path} =====\n{content}" for path, content in downloaded.items())
    return {
        "status": "DOWNLOADED",
        "files": downloaded,
        "full_content": full_content,
        "file_count": len(downloaded),
        "sha256": hashlib.sha256(full_content.encode("utf-8")).hexdigest(),
        "revision": payload.get("sha"),
    }


def _github_reference(url: str) -> dict[str, str] | None:
    parsed = urlparse(url)
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if len(parts) < 4 or parts[2] not in {"tree", "blob"}:
        return {"owner": owner, "repo": repo, "ref": "main", "path": ""}
    return {"owner": owner, "repo": repo, "ref": parts[3], "path": "/".join(parts[4:])}


def _source_url(item: dict[str, Any]) -> str:
    for key in ("install_url", "installUrl", "url", "pageUrl", "detailUrl", "source_url", "sourceUrl", "repository"):
        value = _text(item.get(key))
        if value.startswith(("http://", "https://")):
            return value
    return ""


def _identity(name: str, source_url: str) -> str:
    value = source_url or name
    return re.sub(r"[^a-z0-9]+", "", value.lower())[:240]


def _is_under(path: str, prefix: str) -> bool:
    return not prefix or path == prefix or path.startswith(f"{prefix}/")


def _is_text_path(path: str) -> bool:
    name = Path(path).name.lower()
    return name in {"skill.md", "readme.md", "license"} or Path(path).suffix.lower() in TEXT_EXTENSIONS


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _tags(value: Any) -> list[str]:
    if isinstance(value, str):
        values = re.split(r"[,;/\s]+", value)
    elif isinstance(value, list):
        values = [str(item) for item in value if item]
    else:
        values = []
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))[:20]
