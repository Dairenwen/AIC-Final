#!/usr/bin/env python3
"""Copy supplied assets into target persistent storage without deleting files.

Default lists counts only. Use --apply on Linux after mounting/transferring the
source material. Existing different files abort the entire preflight; identical
files are reused. PDF payloads go directly to their final directory, once.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').digest()


def plan_files(source: Path, destination: Path):
    groups = (
        ('uploads', source / '补充/uploads', destination / 'uploads'),
        ('generated', source / '补充/generated', destination / 'generated'),
        ('metadata', source / '补充/metadata', destination / 'knowledge-base/metadata'),
        ('index', source / '补充/demov15_milvus/milvus_lite.db', destination / 'knowledge-base/milvus_lite.db'),
        ('papers', source / '文献/arxiv', destination / 'knowledge-base/pdfs'),
        ('formula-model', source / 'backend/app/tools/formula_recognition/runtime/unimernet/models/unimernet_base',
         destination / 'models/unimernet_base'),
    )
    plan = []
    for group, origin, target in groups:
        if not origin.is_dir():
            raise ValueError(f'Missing source group: {group}')
        for item in sorted(origin.rglob('*')):
            relative = item.relative_to(origin)
            if any(part.startswith('.') or part == '__MACOSX' for part in relative.parts):
                continue
            if item.is_symlink():
                raise ValueError(f'Symlink source rejected in {group}')
            if not item.is_file():
                continue
            # A copied live lock file is not a persisted Milvus data asset.
            if group == 'index' and item.name == 'LOCK':
                continue
            if group == 'papers' and item.suffix.lower() != '.pdf':
                continue
            output = target / relative
            if not output.resolve().is_relative_to(destination.resolve()):
                raise ValueError(f'Target symlink escape in {group}')
            plan.append((group, item, output))
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, default=Path('/srv/zhiyan-data'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--owner', default='zhiyan', help='Existing Linux service account that must own restored data.')
    args = parser.parse_args()
    if args.apply and (sys.platform != 'linux' or not args.destination.is_absolute()):
        parser.error('Apply requires Linux and an absolute persistent data directory.')
    plan = plan_files(args.source.resolve(), args.destination)
    groups = sorted({group for group, _, _ in plan})
    for group in groups:
        items = [item for label, item, _ in plan if label == group]
        print(f'{group}: {len(items)} files; {sum(item.stat().st_size for item in items)} bytes')
    if not args.apply:
        print('Inspection only. No files copied or removed.')
        return 0
    import pwd
    owner = pwd.getpwnam(args.owner)
    if os.geteuid() not in (0, owner.pw_uid):
        parser.error('Run as the specified service account or root so restored files are readable by the service.')
    pending = []
    for _, item, output in plan:
        if output.exists():
            if not output.is_file() or digest(item) != digest(output):
                raise ValueError('Target has different content; no overwrite performed.')
        else:
            pending.append((item, output))
    for item, output in pending:
        output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents overwriting a file created after preflight.
        with item.open('rb') as incoming, output.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
        output.chmod(0o600)
        if digest(item) != digest(output):
            raise ValueError('Copy verification failed; inspect target before continuing.')
    # Only paths in the reviewed copy plan and their persistent-data parents.
    # This also fixes files copied by root during an earlier interrupted run.
    for _, _, output in plan:
        os.chown(output, owner.pw_uid, owner.pw_gid)
        parent = output.parent
        while parent.is_relative_to(args.destination) and parent != args.destination.parent:
            os.chown(parent, owner.pw_uid, owner.pw_gid)
            if parent == args.destination:
                break
            parent = parent.parent
    print(f'Copied and SHA-256 verified {len(pending)} files. No deletions or model calls.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
