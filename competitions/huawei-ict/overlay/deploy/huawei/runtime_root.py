#!/usr/bin/env python3
"""Resolve private data root only for a verified snapshot reentry."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import sys


def resolve(code_root: Path, environ: dict[str, str]) -> Path:
    code_root = code_root.resolve(strict=True)
    if environ.get("ZHIYAN_SNAPSHOT_REENTRY") != "1":
        return code_root
    candidate_value = environ.get("ZHIYAN_DEPLOY_ROOT", "")
    if not candidate_value:
        raise ValueError("快照重入缺少私有数据根目录")
    candidate = Path(candidate_value).resolve(strict=True)
    state_path = candidate / "deploy/huawei/.env.deploy-state.json"
    info = state_path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("部署状态文件类型或权限不安全")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("部署状态文件格式无效")
    identity = state.get("release_id", "")
    if state.get("version") != 1 or state.get("status") != "started" or not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise ValueError("快照重入缺少有效的已启动状态")
    expected = candidate.parent / ".zhiyan-releases" / identity
    if expected.is_symlink() or expected.resolve(strict=True) != code_root:
        raise ValueError("快照与私有数据目录的登记记录不匹配")
    return candidate


def main() -> int:
    try:
        print(resolve(Path(sys.argv[1]), dict(os.environ)))
    except (IndexError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"无法确定部署数据目录：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
