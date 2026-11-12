#!/usr/bin/env python3
"""Retryable production bootstrap without removing or implicitly upgrading data."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
_release_spec = importlib.util.spec_from_file_location("zhiyan_release_snapshot", Path(__file__).with_name("release_snapshot.py"))
release_snapshot = importlib.util.module_from_spec(_release_spec)
_release_spec.loader.exec_module(release_snapshot)

STATE_NAME = ".env.deploy-state.json"  # excluded by the delivery packager
LOCK_NAME = ".env.deploy-state.lock"


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(root: Path, source_root: Path | None = None) -> dict[str, str]:
    source_root = source_root or root
    package_path = source_root / "scripts/package_delivery.py"
    spec = importlib.util.spec_from_file_location("delivery_fingerprint", package_path)
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)
    digest = hashlib.sha256()
    for relative in ("backend", "frontend", "deploy", "scripts", ".codeartsdoer", ".dockerignore"):
        source = source_root / relative
        for item in package.files(source) if source.is_dir() else [source]:
            digest.update(str(item.relative_to(source_root)).encode("utf-8"))
            digest.update(b"\0")
            digest.update(bytes.fromhex(hash_file(item)))
    here = root / "deploy/huawei"
    # Private data is tracked using metadata to avoid re-reading the 31 GB
    # corpus on each restart. The DB dump itself is fully hashed.
    assets = hashlib.sha256()
    for relative in ("文献/arxiv", "补充/uploads", "补充/generated", "补充/metadata",
                     "补充/demov15_milvus", "补充/docling_hf", "补充/literature_ppt_tools"):
        source = root / relative
        if not source.exists():
            assets.update((relative + "\0missing\0").encode())
            continue
        for current, dirs, files in os.walk(source, followlinks=False):
            folder = Path(current)
            dirs[:] = sorted(d for d in dirs if not package.excluded(folder / d) and not (folder / d).is_symlink())
            for name in sorted(files):
                item = folder / name
                if package.excluded(item):
                    continue
                info = item.lstat()
                assets.update(str(item.relative_to(root)).encode("utf-8") + b"\0")
                assets.update(f"{info.st_mode}:{info.st_size}:{info.st_mtime_ns}".encode() + b"\0")
                if item.is_symlink():
                    assets.update(os.readlink(item).encode("utf-8") + b"\0")
    return {
        "source_sha256": digest.hexdigest(),
        "configuration_sha256": hash_file(here / ".env.production"),
        "certificate_sha256": hash_file(here / "tls/fullchain.pem"),
        "certificate_key_sha256": hash_file(here / "tls/privkey.pem"),
        "database_backup_sha256": hash_file(root / "补充/zhiyan_2026-10-09_11-47-01_pgsql_data.sql"),
        "asset_metadata_sha256": assets.hexdigest(),
    }


def save_state(path: Path, state: dict) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=".env.deploy-state.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(state, target, sort_keys=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp_name, path)
        dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def load_state(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise RuntimeError("部署状态文件类型或权限不安全，请检查")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or data.get("status") not in ("initializing", "started"):
        raise RuntimeError("部署状态文件无效，请检查；不会接管已有数据")
    return data


def run(command: list[str], *, capture=False, check=True):
    return subprocess.run(command, check=check, text=True, capture_output=capture)


def compose_command(root: Path, release: Path) -> list[str]:
    here = root / "deploy/huawei"
    return ["docker", "compose", "--project-name", "zhiyan-production", "--env-file",
            str(here / ".env.production"), "-f", str(release / "deploy/docker/docker-compose.yml"),
            "-f", str(release / "deploy/huawei/docker-production.yml")]


def effective_fingerprint(root: Path, compose: list[str], release: Path) -> dict[str, str]:
    result = fingerprint(root, release)
    # Compose interpolation can be overridden by the invoking shell. Hash the
    # rendered config privately as well, without logging or storing its values.
    rendered = run(compose + ["config", "--format", "json"], capture=True).stdout
    result["effective_compose_sha256"] = hashlib.sha256(rendered.encode()).hexdigest()
    return result


def container_snapshot(compose: list[str]) -> dict:
    services = set(run(compose + ["config", "--services"], capture=True).stdout.splitlines())
    identifiers = run(compose + ["ps", "-aq"], capture=True).stdout.splitlines()
    result = {}
    for identifier in identifiers:
        info = run(["docker", "inspect", "--format",
            '{{index .Config.Labels "com.docker.compose.service"}}|{{index .Config.Labels "com.docker.compose.config-hash"}}|{{.Id}}|{{.Image}}|{{.Config.Image}}',
            identifier], capture=True).stdout.strip().split("|")
        if len(info) != 5 or info[0] not in services or info[0] in result or not all(info):
            raise RuntimeError("发现无法识别或重复的生产容器；停止以避免覆盖")
        result[info[0]] = {"config": info[1], "container": info[2], "image": info[3], "reference": info[4]}
    return {"services": sorted(services), "containers": result}


def built_image_snapshot(compose: list[str]) -> dict[str, str]:
    rendered = json.loads(run(compose + ["config", "--format", "json"], capture=True).stdout)
    result = {}
    for service, config in rendered["services"].items():
        if "build" not in config:
            continue
        reference = config.get("image") or f"zhiyan-production-{service}"
        image_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", reference], capture=True).stdout.strip()
        if not image_id:
            raise RuntimeError("构建镜像身份无法确认")
        result[reference] = image_id
    if not result:
        raise RuntimeError("没有找到应用构建镜像")
    return result


def unmanaged_resources(compose: list[str]) -> bool:
    if run(compose + ["ps", "-aq"], capture=True).stdout.strip():
        return True
    # Listing errors must fail closed. A failed individual volume inspect is
    # not reliable evidence that a volume does not exist.
    volumes = set(run(["docker", "volume", "ls", "--format", "{{.Name}}"], capture=True).stdout.splitlines())
    for name in ("zhiyan-pgdata", "zhiyan-data", "zhiyan-esdata", "mermaid-ipc"):
        if "zhiyan-production_" + name in volumes:
            return True
    return False


def _deploy(root: Path, domain: str, *, accept_initial_changes=False) -> None:
    here = root / "deploy/huawei"
    path = here / STATE_NAME
    releases_root = root.parent / ".zhiyan-releases"
    lock_fd = os.open(here / LOCK_NAME, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(lock_fd, "r+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("另一个部署任务正在执行") from exc
        os.environ.update(ZHIYAN_DEPLOY_ROOT=str(root), ZHIYAN_ENV_FILE=str(here / ".env.production"))
        if path.exists():
            state = load_state(path)
            identity = state.get("release_id", "")
            if not isinstance(identity, str) or len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
                raise RuntimeError("部署状态缺少有效的发布快照身份；不会自动接管已有数据")
            release = releases_root / identity
            release_snapshot.verify_release(release, identity)
            if state["status"] == "initializing":
                current_source_id = release_snapshot.release_id(release_snapshot.source_manifest(root))
                if current_source_id != identity:
                    if not accept_initial_changes:
                        raise RuntimeError("初始化期间源码已变化；需人工核对后显式确认")
                    release, identity = release_snapshot.create_release(root, releases_root)
                    state["release_id"] = identity
                    state["accepted_initial_change"] = True
                    state.pop("built_images", None)
            os.environ["ZHIYAN_RELEASE_DIR"] = str(release)
            compose = compose_command(root, release)
            current = effective_fingerprint(root, compose, release)
            if state.get("fingerprint") != current:
                if state["status"] != "initializing" or not accept_initial_changes:
                    raise RuntimeError("源码、配置、证书或数据来源已变化；拒绝静默升级。初始化失败后的变更需人工核对并显式确认")
                state["fingerprint"] = current
                state["accepted_initial_change"] = True
                state.pop("built_images", None)
                save_state(path, state)
            elif accept_initial_changes and state["status"] == "started":
                raise RuntimeError("已启动的版本不接受初始化变更选项；升级需另行处理")
        else:
            if accept_initial_changes:
                raise RuntimeError("首次部署不能使用初始化变更重试选项")
            os.environ["ZHIYAN_RELEASE_DIR"] = str(root)
            compose = compose_command(root, root)
            if unmanaged_resources(compose):
                raise RuntimeError("存在没有登记的生产容器或持久卷；停止以避免接管已有数据")
            print("创建并核验独立代码快照；私有数据和密钥不会复制到快照。", flush=True)
            release, identity = release_snapshot.create_release(root, releases_root)
            os.environ["ZHIYAN_RELEASE_DIR"] = str(release)
            compose = compose_command(root, release)
            current = effective_fingerprint(root, compose, release)
            state = {"version": 1, "status": "initializing", "fingerprint": current, "release_id": identity}
            save_state(path, state)

        if state["status"] == "started":
            actual = container_snapshot(compose)
            if actual != state.get("containers") or set(actual["containers"]) != set(actual["services"]):
                raise RuntimeError("现有容器与已登记的启动版本不一致；禁止自动替换或升级")
            print("启动已登记快照；原源码改动不会进入运行版本，也不会重新构建或替换容器。", flush=True)
            run(compose + ["up", "-d", "--no-build", "--no-recreate"])
        else:
            print("启动初始化版本；失败可按相同配置重试，不删除数据卷。", flush=True)
            if "built_images" not in state:
                run(compose + ["build"])
                release_snapshot.verify_release(release, identity)
                if effective_fingerprint(root, compose, release) != current:
                    raise RuntimeError("构建期间源码、配置、证书或数据来源发生变化；停止启动")
                state["built_images"] = built_image_snapshot(compose)
                save_state(path, state)
            elif built_image_snapshot(compose) != state["built_images"]:
                raise RuntimeError("已构建的镜像已被替换或删除；停止重试以避免静默升级")
            if not state.get("accepted_initial_change"):
                for info in container_snapshot(compose)["containers"].values():
                    image = run(["docker", "image", "inspect", "--format", "{{.Id}}", info["reference"]], capture=True).stdout.strip()
                    if image != info["image"]:
                        raise RuntimeError("现有初始化容器的镜像标签已变化；停止重试")
            run(compose + ["up", "-d", "--no-build"])
            state.pop("accepted_initial_change", None)
            state["containers"] = container_snapshot(compose)
            save_state(path, state)
        run(compose + ["exec", "-T", "backend", "python", "/srv/zhiyan/deploy/huawei/verify.py",
                       "--base-url", "http://127.0.0.1:5000", "--attempts", "20", "--interval", "3", "--core-only"])
        run(["curl", "--noproxy", "*", "--fail", "--silent", "--show-error", "--output", "/dev/null", "--max-time", "15",
             "--retry", "10", "--retry-all-errors", "--retry-delay", "3",
             "--resolve", f"{domain}:443:127.0.0.1", f"https://{domain}/"])
        print("建立并核对论文关键词索引，不调用模型。", flush=True)
        run(compose + ["exec", "-T", "backend", "python", "/srv/zhiyan/deploy/docker/bootstrap_search_index.py"])
        release_snapshot.verify_release(release, identity)
        if effective_fingerprint(root, compose, release) != current:
            raise RuntimeError("启动检查期间源码、配置、证书或数据来源发生变化；未记录成功")
        snapshot = container_snapshot(compose)
        if set(snapshot["containers"]) != set(snapshot["services"]):
            raise RuntimeError("生产容器不完整；未记录成功")
        state.update(status="started", containers=snapshot)
        save_state(path, state)
        print("基础启动与关键词索引检查通过；这不代表全部业务或外部模型已验收。", flush=True)


def deploy(root: Path, domain: str, *, accept_initial_changes=False) -> None:
    keys = ("ZHIYAN_DEPLOY_ROOT", "ZHIYAN_ENV_FILE", "ZHIYAN_RELEASE_DIR")
    original = {key: os.environ.get(key) for key in keys}
    try:
        _deploy(root, domain, accept_initial_changes=accept_initial_changes)
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--accept-initial-changes", action="store_true")
    args = parser.parse_args()
    try:
        deploy(args.root.resolve(), args.domain, accept_initial_changes=args.accept_initial_changes)
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        # Do not print subprocess output or the state file: they may contain secrets.
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(f"部署未完成：{message}；未删除卷或数据。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
