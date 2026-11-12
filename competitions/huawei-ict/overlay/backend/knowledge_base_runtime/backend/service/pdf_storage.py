"""Read restored PDFs and write new uploads without changing the archive."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass

from knowledge_base_runtime.backend.config.settings import PDF_DIR, REFERENCE_PDF_DIR


def validate_paper_id(value: str) -> str:
    if (not value or value in {".", ".."} or value.startswith(".")
            or "/" in value or "\\" in value
            or any(unicodedata.category(ch).startswith("C") for ch in value)
            or len((value + ".pdf").encode("utf-8")) > 255):
        raise ValueError("论文 ID 必须是安全的单个文件名")
    return value


def find_pdf(key: str) -> Path | None:
    name = Path(key).name
    if not name or name in {".", ".."}:
        return None
    paths = [PDF_DIR / name]
    if REFERENCE_PDF_DIR != PDF_DIR:
        paths.append(REFERENCE_PDF_DIR / name)
    found = []
    for path in paths:
        if path.is_symlink():
            raise ValueError("论文文件为符号链接，无法安全读取")
        if path.is_file():
            found.append(path)
    if len(found) > 1:
        raise ValueError("上传目录与历史论文目录存在同名文件，请先核对")
    return found[0] if found else None


@dataclass(frozen=True)
class PublishedPdf:
    path: Path
    device: int
    inode: int
    fd: int

    def close(self) -> None:
        os.close(self.fd)


def store_pdf(source: Path, paper_id: str, name: str | None = None) -> PublishedPdf:
    id_name = validate_paper_id(paper_id) + ".pdf"
    if name is None:
        name = id_name
    if not name.endswith(".pdf") or validate_paper_id(name[:-4]) + ".pdf" != name:
        raise ValueError("论文文件名不安全")
    target = PDF_DIR / name
    reference = REFERENCE_PDF_DIR / id_name
    legacy = PDF_DIR / id_name
    if (reference.exists() or reference.is_symlink() or legacy.exists() or legacy.is_symlink()
            or target.exists() or target.is_symlink()):
        raise ValueError("论文文件已存在，不能覆盖")
    fd, temp_name = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=PDF_DIR)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as incoming:
            shutil.copyfileobj(incoming, out, length=1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        try:
            # link() publishes the complete file only if the destination does
            # not exist, so simultaneous uploads cannot replace one another.
            os.link(temp, target, follow_symlinks=False)
        except FileExistsError as exc:
            raise ValueError("论文文件已存在，不能覆盖") from exc
        # Keep the published inode open until commit or cleanup. This prevents
        # inode reuse from making an unrelated replacement look like our file.
        file_fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(file_fd)
        temp_info = temp.lstat()
        if (info.st_dev, info.st_ino) != (temp_info.st_dev, temp_info.st_ino):
            os.close(file_fd)
            raise ValueError("论文文件在发布时发生变化")
        try:
            dir_fd = os.open(PDF_DIR, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            os.close(file_fd)
            raise
        return PublishedPdf(target, info.st_dev, info.st_ino, file_fd)
    finally:
        temp.unlink(missing_ok=True)


def remove_own_pdf(published: PublishedPdf) -> None:
    """Remove only the unchanged file this process just created."""
    try:
        info = published.path.lstat()
        held = os.fstat(published.fd)
        if (not published.path.is_symlink() and (held.st_dev, held.st_ino) == (published.device, published.inode)
                and info.st_dev == published.device
                and info.st_ino == published.inode and info.st_nlink == 1):
            published.path.unlink()
    except FileNotFoundError:
        pass
