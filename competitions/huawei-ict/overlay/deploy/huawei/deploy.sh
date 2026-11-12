#!/usr/bin/env bash
# Unified Docker deployment entry point.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE_ROOT="$(cd "$HERE/../.." && pwd)"
ROOT="$(python3 "$HERE/runtime_root.py" "$CODE_ROOT")" || exit 1
# Once initialized, route subsequent starts through the frozen release. The
# small bootstrap checks the manifest and verifier code before executing it.
if [[ "${1:---check}" == --apply && -z "${ZHIYAN_SNAPSHOT_REENTRY:-}" && -e "$ROOT/deploy/huawei/.env.deploy-state.json" ]]; then
  RELEASE="$(python3 - "$ROOT" <<'PY'
import hashlib, importlib.util, json, re, stat, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
path = root / 'deploy/huawei/.env.deploy-state.json'
info = path.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
    raise SystemExit('部署状态文件类型或权限不安全。')
state = json.loads(path.read_text(encoding='utf8'))
if state.get('version') != 1 or state.get('status') not in ('initializing', 'started'):
    raise SystemExit('部署状态文件无效。')
if state['status'] == 'started':
    identity = state.get('release_id', '')
    if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{64}', identity):
        raise SystemExit('部署状态缺少有效快照身份。')
    release = root.parent / '.zhiyan-releases' / identity
    manifest_path = release / '.zhiyan-release.json'
    if release.is_symlink() or manifest_path.is_symlink():
        raise SystemExit('发布快照路径异常。')
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != identity:
        raise SystemExit('发布快照清单已损坏。')
    manifest = json.loads(raw)
    entries = {item['path']: item for item in manifest['files']}
    for relative in ('deploy/huawei/deploy.sh', 'deploy/huawei/deploy-docker.sh', 'deploy/huawei/runtime_root.py',
                     'deploy/huawei/deployment_transaction.py', 'deploy/huawei/release_snapshot.py'):
        item = release / relative
        if item.is_symlink() or not item.is_file() or relative not in entries:
            raise SystemExit('发布快照中的启动文件缺失。')
        if hashlib.sha256(item.read_bytes()).hexdigest() != entries[relative]['sha256']:
            raise SystemExit('发布快照中的启动文件已损坏。')
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location('release_check', release / 'deploy/huawei/release_snapshot.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.verify_release(release, identity)
    print(release)
PY
)" || { echo '发布快照检查失败；未启动或修改数据。' >&2; exit 1; }
  if [[ -n "$RELEASE" ]]; then
    exec env ZHIYAN_DEPLOY_ROOT="$ROOT" ZHIYAN_SNAPSHOT_REENTRY=1 bash "$RELEASE/deploy/huawei/deploy.sh" "$@"
  fi
fi
case "${1:---check}" in
  --check|--apply) exec bash "$HERE/deploy-docker.sh" "$@" ;;
  *) echo '用法: bash deploy/huawei/deploy.sh [--check|--apply]' >&2; exit 2 ;;
esac
