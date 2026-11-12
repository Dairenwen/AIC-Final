#!/usr/bin/env python3
"""Restore a tree without overwriting user files or exposing partial copies."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import sys


def ensure_directory(path: Path, base: Path) -> None:
    """Do not follow an existing symlink inside the writable data tree."""
    relative = path.relative_to(base)
    current = base
    for part in relative.parts:
        current = current / part
        try:
            current.mkdir()
        except FileExistsError:
            pass
        if not stat.S_ISDIR(current.lstat().st_mode):
            raise ValueError(f"目标目录不是普通目录：{current}")


def check_directory(path: Path, base: Path) -> None:
    current = base
    for part in path.relative_to(base).parts:
        current = current / part
        if not stat.S_ISDIR(current.lstat().st_mode):
            raise ValueError(f"目标目录不是普通目录：{current}")


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_existing(source: Path, target: Path) -> None:
    source_stat, target_stat = source.lstat(), target.lstat()
    if stat.S_ISLNK(source_stat.st_mode):
        if stat.S_ISLNK(target_stat.st_mode) and os.readlink(source) == os.readlink(target):
            return
    elif stat.S_ISREG(source_stat.st_mode) and stat.S_ISREG(target_stat.st_mode):
        if source_stat.st_size == target_stat.st_size and file_digest(source) == file_digest(target):
            return
    raise ValueError(f"目标文件已存在但与交接文件不同，已保留并停止恢复：{target}")


def stream_copy(source, target):
    for block in iter(lambda: source.read(1024 * 1024), b""):
        target.write(block)


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def restore_one(source: Path, target: Path, base: Path, staging: Path, source_root: Path) -> None:
    mode = source.lstat().st_mode
    if stat.S_ISLNK(mode):
        link = os.readlink(source)
        if os.path.isabs(link) or not source.resolve(strict=True).is_relative_to(source_root):
            raise ValueError(f"交接文件中的链接超出来源目录：{source}")
        try:
            os.symlink(link, target)
            sync_directory(target.parent)
        except FileExistsError:
            check_existing(source, target)
        return
    if not stat.S_ISREG(mode):
        raise ValueError(f"不支持的交接文件类型：{source}")

    staged_name = hashlib.sha256(str(target.relative_to(base)).encode()).hexdigest()
    staged = staging / staged_name
    try:
        check_existing(source, target)
    except FileNotFoundError:
        pass
    else:
        # A process may have stopped after linking a complete file into place.
        if staged.exists() and stat.S_ISREG(staged.lstat().st_mode):
            staged.unlink()
        return

    # A killed copy can leave a staging file. Remove only an unlinked regular
    # staging file before retrying; never truncate a file that might be linked
    # to a completed destination or to unrelated data.
    try:
        old_stage = staged.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(old_stage.st_mode) or old_stage.st_nlink != 1:
            raise ValueError(f"恢复临时文件状态异常，停止恢复：{staged}")
        staged.unlink()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(staged, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as input_file:
            before = os.fstat(input_file.fileno())
            stream_copy(input_file, output)
            os.fchmod(output.fileno(), stat.S_IMODE(mode))
            output.flush()
            os.fsync(output.fileno())
            after = os.fstat(input_file.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError(f"交接文件复制过程中发生变化：{source}")
        if staged.stat().st_size != before.st_size or file_digest(source) != file_digest(staged):
            raise ValueError(f"交接文件复制校验失败：{source}")
        try:
            # Hard-linking within the data volume is atomic and fails rather
            # than replacing a destination that appeared concurrently.
            os.link(staged, target, follow_symlinks=False)
            sync_directory(target.parent)
        except FileExistsError:
            check_existing(source, target)
    finally:
        # Only this function's deterministic staging file is removed.
        try:
            staged.unlink()
        except FileNotFoundError:
            pass


def resolve_roots(source: Path, destination: Path, base: Path):
    source = source.resolve(strict=True)
    # Resolve only the data root. Resolving a destination path would follow
    # attacker-controlled links inside that root before we can reject them.
    base_argument = Path(os.path.abspath(base))
    destination_relative = Path(os.path.abspath(destination)).relative_to(base_argument)
    base = base_argument.resolve(strict=True)
    destination = base / destination_relative
    if not source.is_dir():
        raise ValueError("交接来源不是目录")
    return source, destination, base


def verify_tree(source: Path, destination: Path, base: Path) -> None:
    """Read-only comparison of an earlier restoration with its source."""
    source, destination, base = resolve_roots(source, destination, base)
    check_directory(destination, base)
    for current, dirs, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        target_dir = destination / current_path.relative_to(source)
        check_directory(target_dir, base)
        symlink_dirs = [name for name in dirs if (current_path / name).is_symlink()]
        dirs[:] = sorted(name for name in dirs if name not in symlink_dirs)
        for name in sorted(files + symlink_dirs):
            item = current_path / name
            if item.is_symlink() and (os.path.isabs(os.readlink(item)) or
                    not item.resolve(strict=True).is_relative_to(source)):
                raise ValueError(f"交接文件中的链接超出来源目录：{item}")
            check_existing(item, target_dir / name)


def restore_tree(source: Path, destination: Path, base: Path, state: Path) -> None:
    base_argument = Path(os.path.abspath(base))
    state_relative = Path(os.path.abspath(state)).relative_to(base_argument)
    source, destination, base = resolve_roots(source, destination, base)
    state = base / state_relative
    ensure_directory(destination, base)
    ensure_directory(state, base)
    staging = state / "restore-staging"
    ensure_directory(staging, base)
    lock = state / "restore.lock"
    lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(lock_fd, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        for current, dirs, files in os.walk(source, followlinks=False):
            current_path = Path(current)
            target_dir = destination / current_path.relative_to(source)
            ensure_directory(target_dir, base)
            # os.walk puts symlinked directories in dirs; restore them as links.
            symlink_dirs = [name for name in dirs if (current_path / name).is_symlink()]
            dirs[:] = sorted(name for name in dirs if name not in symlink_dirs)
            for name in sorted(files + symlink_dirs):
                restore_one(current_path / name, target_dir / name, base, staging, source)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.verify_only:
            verify_tree(args.source, args.destination, args.base)
            print("[zhiyan] 文件比对通过；未修改数据")
        else:
            if not args.state_dir:
                parser.error("恢复时必须指定 --state-dir")
            restore_tree(args.source, args.destination, args.base, args.state_dir)
    except (OSError, ValueError) as exc:
        print(f"[zhiyan] 文件恢复失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
