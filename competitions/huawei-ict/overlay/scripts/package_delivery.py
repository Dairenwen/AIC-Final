#!/usr/bin/env python3
"""Build source, private data, or complete private tar archives without changing inputs."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache',
                 '.mypy_cache', '.ruff_cache', '.cache', '.puppeteer-cache', '.uv-cache',
                 'dist', '_archive_previous_setup'}


def excluded(path: Path) -> bool:
    if path.name in EXCLUDED_DIRS or path.name.startswith(('.venv-', '.venv_')) or path.name in {'.DS_Store', 'Thumbs.db'}:
        return True
    if path in {ROOT / 'backend' / name for name in ('generated', 'uploads', 'var')}:
        return True
    if path == ROOT / '补充/docling_hf':
        return True  # Added separately by complete mode with validated model-cache links.
    if path.name.startswith('.env') and not path.name.endswith('.example'):
        return True
    return path.suffix.lower() in {'.pyc', '.pyo', '.pem', '.key', '.p12'}


def files(root: Path):
    for current, dirs, names in os.walk(root, followlinks=False):
        folder = Path(current)
        dirs[:] = sorted(d for d in dirs if not excluded(folder / d))
        for name in dirs:
            if (folder / name).is_symlink():
                raise RuntimeError(f'不支持符号链接目录: {(folder / name).relative_to(ROOT)}')
        for name in sorted(names):
            path = folder / name
            if excluded(path):
                continue
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(f'不支持特殊文件或符号链接: {path.relative_to(ROOT)}')
            yield path


def docling_entries(root: Path):
    """Include the offline model cache while preserving only safe relative links."""
    if root.is_symlink() or not (root / 'hub').is_dir():
        raise FileNotFoundError(f'缺少必要的 Docling 模型缓存: {root}')
    boundary = root.resolve(strict=True)
    for current, dirs, names in os.walk(root, followlinks=False):
        folder = Path(current)
        for name in sorted(dirs + names):
            path = folder / name
            if excluded(path):
                continue
            if path.is_symlink():
                target = os.readlink(path)
                if os.path.isabs(target):
                    raise ValueError(f'Docling 模型缓存含绝对符号链接: {path}')
                try:
                    resolved = path.resolve(strict=True)
                    relative = resolved.relative_to(boundary)
                except (FileNotFoundError, RuntimeError, ValueError) as exc:
                    raise ValueError(f'Docling 模型缓存含无效或越界符号链接: {path}') from exc
                # A link must not point to anything deliberately omitted from the archive.
                if any(excluded(root.joinpath(*relative.parts[:i])) for i in range(1, len(relative.parts) + 1)):
                    raise ValueError(f'Docling 模型缓存链接指向已排除文件: {path}')
                yield path
            elif path.is_dir() or path.is_file():
                yield path
            else:
                raise ValueError(f'Docling 模型缓存含特殊文件: {path}')
        dirs[:] = sorted(d for d in dirs if not excluded(folder / d) and not (folder / d).is_symlink())


def create_archive(destination: Path, sources: list[Path], *, docling_root: Path | None = None) -> None:
    # Check required sources before creating an output file.
    for source in sources:
        if not source.exists() or source.is_symlink():
            raise FileNotFoundError(f'缺少必要输入或输入为符号链接: {source}')
    if docling_root is not None and (docling_root.is_symlink() or not (docling_root / 'hub').is_dir()):
        raise FileNotFoundError(f'缺少必要的 Docling 模型缓存: {docling_root}')
    if destination.exists():
        raise FileExistsError(f'输出已存在，不覆盖: {destination}')
    # Exclusive create prevents accidental replacement; chmod protects private data.
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    count = 0
    with os.fdopen(fd, 'wb') as output, tarfile.open(fileobj=output, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for source in sources:
            entries = files(source) if source.is_dir() else [source]
            for item in entries:
                archive.add(item, arcname=str(item.relative_to(ROOT)), recursive=False)
                count += 1
        if docling_root is not None:
            for item in docling_entries(docling_root):
                archive.add(item, arcname=str(item.relative_to(ROOT)), recursive=False)
                count += 1
    digest = hashlib.sha256()
    with destination.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    print(f'{destination.name}: {count} files, {destination.stat().st_size} bytes, SHA256 {digest.hexdigest()}', flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='已存在的私有输出目录')
    parser.add_argument('--part', choices=['code', 'data', 'complete'], required=True)
    args = parser.parse_args()
    output = args.output.resolve(strict=True)
    if not output.is_dir():
        parser.error('输出位置必须是目录')
    if (ROOT == output or ROOT in output.parents) and not (
        args.part == 'data' and output == ROOT / '文献'
    ):
        parser.error('输出目录不能在源码或数据源目录内；私有数据包可放在文献目录')
    if args.part == 'code':
        sources = [ROOT / p for p in ('backend', 'frontend', 'deploy', 'scripts', '.codeartsdoer', '.dockerignore')]
        destination = output / 'zhiyan-code.tar'
    elif args.part == 'data':
        sources = [ROOT / '补充', ROOT / '文献' / 'arxiv']
        destination = output / 'zhiyan-private-data.tar'
    else:
        sources = [ROOT / p for p in ('backend', 'frontend', 'deploy', 'scripts', '.codeartsdoer', '.dockerignore', '补充', '文献/arxiv')]
        destination = output / 'zhiyan-complete-private.tar'
    create_archive(destination, sources, docling_root=ROOT / '补充/docling_hf' if args.part == 'complete' else None)


if __name__ == '__main__':
    main()
