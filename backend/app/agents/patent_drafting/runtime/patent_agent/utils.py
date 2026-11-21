from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SUPPORTED_INPUT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".docx", ".pptx", ".ppsx", ".pdf",
    ".py", ".go", ".java", ".js", ".ts", ".tsx", ".rs", ".c", ".h", ".cpp", ".hpp",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def local_timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d%H%M%S")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def list_case_files(case_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(case_dir.rglob("*")):
        if not path.is_file() or any(part.startswith(".") for part in path.relative_to(case_dir).parts):
            continue
        if path.name in {"patent_point_selection_response.json"}:
            continue
        if path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES or path.name in {"case.yaml", "case.yml"}:
            files.append(path)
    return files


def hash_case(case_dir: Path) -> str:
    h = hashlib.sha256()
    for path in list_case_files(case_dir):
        rel = path.relative_to(case_dir).as_posix().encode("utf-8")
        h.update(len(rel).to_bytes(4, "big"))
        h.update(rel)
        data = path.read_bytes()
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_artifact_stem(title: str) -> str:
    title = re.sub(r"\[待填写\]|【待填写】", "", title).strip()
    title = re.sub(r"[\\/:*?\"<>|\r\n]+", "", title)
    title = re.sub(r"\s+", "", title)
    return (title or "技术交底书")[:80]


def redact_text(text: str, secrets: list[str]) -> str:
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, "[REDACTED]")
    return out
