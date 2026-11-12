#!/usr/bin/env python3
"""Create and verify immutable, private-data-free source release snapshots."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys

sys.dont_write_bytecode = True
MANIFEST = ".zhiyan-release.json"
CODE_PATHS = ("backend", "frontend", "deploy", "scripts", ".codeartsdoer", ".dockerignore")


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def source_manifest(root: Path) -> dict:
    spec = importlib.util.spec_from_file_location("release_package_filter", root / "scripts/package_delivery.py")
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)
    entries = []
    for relative in CODE_PATHS:
        path = root / relative
        if not path.exists():
            raise ValueError(f"缺少代码包必要路径：{relative}")
        for item in package.files(path) if path.is_dir() else [path]:
            if item.is_symlink() or not item.is_file():
                raise ValueError("代码快照不接受符号链接或特殊文件")
            entries.append({"path": str(item.relative_to(root)), "sha256": hash_file(item),
                            "mode": 0o444 | (stat.S_IMODE(item.stat().st_mode) & 0o111),
                            "size": item.stat().st_size})
    entries.sort(key=lambda item: item["path"])
    return {"version": 1, "files": entries}


def manifest_bytes(manifest: dict) -> bytes:
    return (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def release_id(manifest: dict) -> str:
    return hashlib.sha256(manifest_bytes(manifest)).hexdigest()


def verify_release(release: Path, expected_id: str, *, require_readonly=True) -> dict:
    if release.is_symlink() or not release.is_dir():
        raise ValueError("发布快照不存在或不是普通目录")
    manifest_path = release / MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("发布快照缺少清单")
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_id:
        raise ValueError("发布快照清单不一致")
    manifest = json.loads(raw)
    if manifest.get("version") != 1 or not isinstance(manifest.get("files"), list):
        raise ValueError("发布快照清单格式无效")
    entries = {item["path"]: item for item in manifest["files"]}
    if len(entries) != len(manifest["files"]):
        raise ValueError("发布快照清单包含重复路径")
    actual = set()
    for current, dirs, names in os.walk(release, followlinks=False):
        folder = Path(current)
        if require_readonly and stat.S_IMODE(folder.lstat().st_mode) & 0o222:
            raise ValueError("发布快照目录可写")
        for name in dirs:
            if (folder / name).is_symlink():
                raise ValueError("发布快照中存在未允许的链接")
        for name in names:
            item = folder / name
            relative = str(item.relative_to(release))
            if relative == MANIFEST:
                if stat.S_IMODE(item.lstat().st_mode) != 0o444:
                    raise ValueError("发布快照清单权限异常")
                continue
            if item.is_symlink() or not item.is_file():
                raise ValueError("发布快照包含特殊文件")
            actual.add(relative)
            entry = entries.get(relative)
            if not entry or item.stat().st_nlink != 1 or stat.S_IMODE(item.stat().st_mode) != entry["mode"] or item.stat().st_size != entry["size"] or hash_file(item) != entry["sha256"]:
                raise ValueError("发布快照文件不一致")
    if actual != set(entries):
        raise ValueError("发布快照文件缺失或包含未登记文件")
    return manifest


def ensure_pending_dir(path: Path, pending: Path) -> None:
    current = pending
    for part in path.relative_to(pending).parts:
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        if current.is_symlink() or not current.is_dir():
            raise ValueError("发布暂存目录包含链接或特殊文件")


def finish_pending(pending: Path, final: Path, identity: str) -> None:
    # Recheck contents before changing directory permissions. A crash while
    # freezing permissions can safely resume from the existing manifest.
    verify_release(pending, identity, require_readonly=False)
    for current, dirs, _ in os.walk(pending, topdown=False, followlinks=False):
        os.chmod(current, 0o555)
    verify_release(pending, identity)
    os.rename(pending, final)


def create_release(source_root: Path, releases_root: Path) -> tuple[Path, str]:
    manifest = source_manifest(source_root)
    identity = release_id(manifest)
    releases_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if releases_root.is_symlink() or not releases_root.is_dir():
        raise ValueError("发布目录必须是普通目录")
    final = releases_root / identity
    if final.exists() or final.is_symlink():
        verify_release(final, identity)
        return final, identity
    pending = releases_root / (".pending-" + identity)
    pending.mkdir(mode=0o700, exist_ok=True)
    if pending.is_symlink() or not pending.is_dir():
        raise ValueError("未完成的发布目录不是普通目录")
    if (pending / MANIFEST).exists():
        finish_pending(pending, final, identity)
        return final, identity
    staging = pending / ".staging"
    staging.mkdir(mode=0o700, exist_ok=True)
    if staging.is_symlink() or not staging.is_dir():
        raise ValueError("发布暂存目录不是普通目录")
    need = sum(entry["size"] for entry in manifest["files"] if not (pending / entry["path"]).exists())
    if shutil.disk_usage(releases_root).free < need + 512 * 1024 * 1024:
        raise ValueError("磁盘空间不足以创建独立代码快照及保留安全余量")
    for entry in manifest["files"]:
        relative = Path(entry["path"])
        target = pending / relative
        ensure_pending_dir(target.parent, pending)
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_file() or target.stat().st_nlink != 1 or target.stat().st_size != entry["size"] or hash_file(target) != entry["sha256"] or stat.S_IMODE(target.stat().st_mode) != entry["mode"]:
                raise ValueError("未完成快照中已有不一致文件，请先人工核查")
            continue
        temp = staging / hashlib.sha256(str(relative).encode()).hexdigest()
        if temp.exists() or temp.is_symlink():
            info = temp.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("发布暂存文件状态异常")
            temp.unlink()
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as output, (source_root / relative).open("rb") as source:
            shutil.copyfileobj(source, output, length=1024 * 1024)
            os.fchmod(output.fileno(), entry["mode"])
            output.flush()
            os.fsync(output.fileno())
        if temp.stat().st_size != entry["size"] or hash_file(temp) != entry["sha256"]:
            raise ValueError("源码在复制期间变化，快照未发布")
        os.rename(temp, target)
    if any(staging.iterdir()):
        raise ValueError("发布暂存目录有未识别文件")
    staging.rmdir()
    if source_manifest(source_root) != manifest:
        raise ValueError("源码在创建快照期间变化，快照未发布")
    manifest_path = pending / MANIFEST
    raw = manifest_bytes(manifest)
    if manifest_path.exists():
        if manifest_path.is_symlink() or manifest_path.read_bytes() != raw:
            raise ValueError("未完成快照清单不一致")
    else:
        fd = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), 0o444)
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    finish_pending(pending, final, identity)
    return final, identity
