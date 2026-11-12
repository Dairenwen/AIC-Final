"""Snapshot is detached from mutable source and never contains private data."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/huawei/release_snapshot.py"
spec = importlib.util.spec_from_file_location("release_snapshot", SCRIPT)
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    for path in ("backend", "frontend", "deploy/huawei/tls", "scripts", ".codeartsdoer", "补充", "文献/arxiv"):
        (root / path).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SCRIPT.parents[2] / "scripts/package_delivery.py", root / "scripts/package_delivery.py")
    (root / ".dockerignore").write_text("ignore")
    (root / "backend/app.py").write_text("original source")
    (root / "deploy/huawei/.env.production").write_text("private-secret")
    (root / "deploy/huawei/tls/privkey.pem").write_text("private-key")
    (root / "补充/private.sql").write_text("private-database")
    (root / "文献/arxiv/paper.pdf").write_text("private-paper")
    yield root
    for path, dirs, _ in os.walk(tmp_path, topdown=False):
        os.chmod(path, 0o700)


def test_snapshot_is_private_data_free_independent_and_verified(source, tmp_path):
    release, identity = snapshot.create_release(source, tmp_path / "releases")
    assert snapshot.verify_release(release, identity)
    assert not (release / "补充").exists()
    assert not (release / "文献").exists()
    assert not (release / "deploy/huawei/.env.production").exists()
    assert not (release / "deploy/huawei/tls/privkey.pem").exists()
    assert (release / "backend/app.py").stat().st_ino != (source / "backend/app.py").stat().st_ino
    (source / "backend/app.py").write_text("new source")
    assert (release / "backend/app.py").read_text() == "original source"
    snapshot.verify_release(release, identity)


@pytest.mark.parametrize("damage", ["changed", "missing", "extra", "writable", "hardlink"])
def test_snapshot_damage_is_rejected(source, tmp_path, damage):
    release, identity = snapshot.create_release(source, tmp_path / "releases")
    item = release / "backend/app.py"
    item.parent.chmod(0o755)
    if damage == "changed":
        item.chmod(0o644)
        item.write_text("tampered")
        item.chmod(0o444)
    elif damage == "missing":
        item.unlink()
    elif damage == "extra":
        (item.parent / "unregistered.py").write_text("unexpected")
    elif damage == "hardlink":
        (source / "backend/app.py").chmod(0o444)
        item.unlink()
        os.link(source / "backend/app.py", item)
    if damage != "writable":
        item.parent.chmod(0o555)
    with pytest.raises(ValueError):
        snapshot.verify_release(release, identity)


def test_interrupted_file_copy_can_resume(source, tmp_path, monkeypatch):
    original = snapshot.shutil.copyfileobj
    def fail_once(input_file, output_file, length):
        output_file.write(input_file.read(3))
        raise OSError("interrupted")
    monkeypatch.setattr(snapshot.shutil, "copyfileobj", fail_once)
    with pytest.raises(OSError, match="interrupted"):
        snapshot.create_release(source, tmp_path / "releases")
    monkeypatch.setattr(snapshot.shutil, "copyfileobj", original)
    release, identity = snapshot.create_release(source, tmp_path / "releases")
    snapshot.verify_release(release, identity)


def test_completed_pending_snapshot_can_resume_after_rename_failure(source, tmp_path, monkeypatch):
    original = snapshot.os.rename
    def fail_final(src, dst):
        if Path(src).name.startswith(".pending-"):
            raise OSError("interrupted")
        return original(src, dst)
    monkeypatch.setattr(snapshot.os, "rename", fail_final)
    with pytest.raises(OSError, match="interrupted"):
        snapshot.create_release(source, tmp_path / "releases")
    monkeypatch.setattr(snapshot.os, "rename", original)
    release, identity = snapshot.create_release(source, tmp_path / "releases")
    snapshot.verify_release(release, identity)


def test_insufficient_space_never_publishes_snapshot(source, tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot.shutil, "disk_usage", lambda path: type("Space", (), {"free": 1})())
    with pytest.raises(ValueError, match="磁盘空间不足"):
        snapshot.create_release(source, tmp_path / "releases")
    assert not any(p for p in (tmp_path / "releases").iterdir() if not p.name.startswith("."))


def test_snapshot_reentry_rejects_non_object_state_cleanly(source):
    module_path = SCRIPT.parent / "runtime_root.py"
    spec = importlib.util.spec_from_file_location("runtime_root_test", module_path)
    resolver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(resolver)
    state = source / "deploy/huawei/.env.deploy-state.json"
    state.write_text("[]")
    state.chmod(0o600)
    with pytest.raises(ValueError, match="格式无效"):
        resolver.resolve(source, {"ZHIYAN_SNAPSHOT_REENTRY": "1", "ZHIYAN_DEPLOY_ROOT": str(source)})


@pytest.mark.skipif(not shutil.which("docker"), reason="Docker Compose is unavailable")
def test_real_compose_uses_snapshot_code_and_original_private_paths(source, tmp_path):
    project = SCRIPT.parents[2]
    (source / "deploy/docker").mkdir()
    for relative in ("deploy/docker/docker-compose.yml", "deploy/huawei/docker-production.yml",
                     "deploy/huawei/nginx-docker-production.conf"):
        shutil.copyfile(project / relative, source / relative)
    env_file = source / "deploy/huawei/.env.production"
    env_file.write_text("POSTGRES_PASSWORD=dummyvalue\nELASTICSEARCH_PASSWORD=dummyvalue_1234567890\n"
                        "PUBLIC_ORIGIN=https://example.org\nZHIYAN_TEST_CONFIG=from-private-env\n")
    release, identity = snapshot.create_release(source, tmp_path / "releases")
    env = dict(os.environ, ZHIYAN_ENV_FILE=str(env_file), ZHIYAN_DEPLOY_ROOT=str(source),
               ZHIYAN_RELEASE_DIR=str(release), ZHIYAN_BACKEND_IMAGE="other-project:latest")
    command = ["docker", "compose", "--project-name", "zhiyan-production", "--env-file", str(env_file),
               "-f", str(release / "deploy/docker/docker-compose.yml"),
               "-f", str(release / "deploy/huawei/docker-production.yml"), "config", "--format", "json"]
    result = subprocess.run(command, check=True, capture_output=True, text=True, env=env)
    rendered = json.loads(result.stdout)
    services = rendered["services"]
    mounts = {item["target"]: item["source"] for item in services["backend"]["volumes"]}
    assert mounts["/srv/zhiyan"] == str(release)
    assert mounts["/restore"] == str(source / "补充")
    assert mounts["/data/knowledge-base/reference-pdfs"] == str(source / "文献/arxiv")
    assert services["backend"]["environment"]["ZHIYAN_TEST_CONFIG"] == "from-private-env"
    assert services["backend"]["build"]["context"] == str(release)
    assert services["web"]["build"]["context"] == str(release)
    web_mounts = {item["target"]: item["source"] for item in services["web"]["volumes"]}
    assert web_mounts["/etc/nginx/conf.d/default.conf"] == str(release / "deploy/huawei/nginx-docker-production.conf")
    assert web_mounts["/etc/nginx/zhiyan-tls"] == str(source / "deploy/huawei/tls")
    db = {item["target"]: item["source"] for item in services["db"]["volumes"]}
    es = {item["target"]: item["source"] for item in services["elasticsearch"]["volumes"]}
    volumes = rendered["volumes"]
    db_volume = volumes[db["/var/lib/postgresql"]]["name"]
    es_volume = volumes[es["/usr/share/elasticsearch/data"]]["name"]
    app_volume = volumes[mounts["/data"]]["name"]
    assert db_volume == "zhiyan-production_zhiyan-pgdata"
    assert es_volume == "zhiyan-production_zhiyan-esdata"
    assert app_volume == "zhiyan-production_zhiyan-data"
    assert len({db_volume, es_volume, app_volume}) == 3
    assert not services["redis"].get("volumes")
    assert not (release / "deploy/huawei/.env.production").exists()
    snapshot.verify_release(release, identity)


def test_entrypoint_uses_snapshot_after_original_source_removed(source, tmp_path):
    project = SCRIPT.parents[2]
    (source / "deploy/docker").mkdir()
    for relative in ("deploy/huawei/deploy.sh", "deploy/huawei/deploy-docker.sh", "deploy/huawei/runtime_root.py",
                     "deploy/huawei/deployment_transaction.py", "deploy/huawei/release_snapshot.py",
                     "deploy/huawei/production_resources.py", "deploy/huawei/verify_certificate.sh",
                     "deploy/huawei/validate_production_environment.py", "deploy/docker/docker-compose.yml",
                     "deploy/huawei/docker-production.yml"):
        shutil.copyfile(project / relative, source / relative)
    weight = source / "backend/app/tools/formula_recognition/runtime/unimernet/models/unimernet_base/pytorch_model.pth"
    weight.parent.mkdir(parents=True)
    weight.write_bytes(b"placeholder-model")
    (source / "补充/zhiyan_2026-10-09_11-47-01_pgsql_data.sql").write_text("backup")
    for directory in ("models--docling-project--docling-layout-heron", "models--docling-project--docling-models"):
        (source / "补充/docling_hf/hub" / directory).mkdir(parents=True)
    env_file = source / "deploy/huawei/.env.production"
    env_file.write_text("\n".join([
        "SECRET_KEY=dummy", "MODEL_CONFIG_ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "POSTGRES_PASSWORD=dummy", "POSTGRES_HOST=rds.internal.example", "REDIS_URL=redis://dcs.internal.example:6379/0",
        "PUBLIC_ORIGIN=https://example.org", "KB_MILVUS_ENABLED=1",
        "ELASTICSEARCH_ENABLED=1", "KB_MILVUS_URI=/tmp/vector", "ELASTICSEARCH_URL=http://elasticsearch:9200",
        "ELASTICSEARCH_USERNAME=elastic", "ELASTICSEARCH_PASSWORD=dummyvalue_1234567890",
        "KB_EMBEDDING_BACKEND=api", "KB_EMBEDDING_API_BASE_URL=https://example.org",
        "KB_EMBEDDING_API_MODEL=embed", "KB_RERANKER_BACKEND=api",
        "KB_RERANKER_API_BASE_URL=https://example.org", "KB_RERANKER_API_MODEL=rerank", "",
    ]))
    env_file.chmod(0o600)
    (source / "deploy/huawei/tls/fullchain.pem").write_text("cert")
    (source / "deploy/huawei/tls/privkey.pem").write_text("key")
    release, identity = snapshot.create_release(source, tmp_path / ".zhiyan-releases")
    state = source / "deploy/huawei/.env.deploy-state.json"
    state.write_text(json.dumps({"version": 1, "status": "started", "release_id": identity}))
    state.chmod(0o600)
    weight.unlink()
    (source / "deploy/huawei/deployment_transaction.py").unlink()
    (source / "deploy/huawei/release_snapshot.py").unlink()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_commands = {
        "uname": "#!/bin/sh\necho Linux\n",
        "docker": "#!/bin/sh\nexit 0\n",
        "curl": "#!/bin/sh\nexit 0\n",
        "cat": "#!/bin/sh\necho 1048576\n",
        "openssl": "#!/bin/sh\ncase \"$1:$*\" in pkey:*\\ -pubin*) /bin/cat >/dev/null;; dgst:*) /bin/cat >/dev/null;; esac\ncase \"$*\" in *-checkhost*) echo 'does match certificate';; *) echo dummy;; esac\n",
        "python3": "#!/bin/sh\ncase \"$1\" in */deployment_transaction.py) echo \"frozen-transaction:$1\"; exit 0;; esac\nexec " + sys.executable + " \"$@\"\n",
    }
    for name, body in fake_commands.items():
        path = fake_bin / name
        path.write_text(body)
        path.chmod(0o755)
    environment = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ["PATH"], PYTHONDONTWRITEBYTECODE="1", ZHIYAN_DEPLOY_ROOT=str(tmp_path / "wrong-data"))
    for name in ("PLATFORM_MODEL_ENABLED", "INSTALL_FORMULA_RUNTIME", "INSTALL_PPT_RUNTIME", "KB_MILVUS_ENABLED",
                 "ELASTICSEARCH_ENABLED", "PUBLIC_ORIGIN", "POSTGRES_PASSWORD", "ELASTICSEARCH_PASSWORD",
                 "DOCKER_DEFAULT_PLATFORM", "COMPOSE_PROFILES", "COMPOSE_ENV_FILES", "COMPOSE_REMOVE_ORPHANS"):
        environment.pop(name, None)
    environment.pop("ZHIYAN_SNAPSHOT_REENTRY", None)
    result = subprocess.run(["bash", str(source / "deploy/huawei/deploy.sh"), "--apply"],
                            capture_output=True, text=True, env=environment)
    assert result.returncode == 0, result.stderr
    assert "frozen-transaction:" + str(release / "deploy/huawei/deployment_transaction.py") in result.stdout
    fake_reentry = dict(environment, ZHIYAN_DEPLOY_ROOT=str(source), ZHIYAN_SNAPSHOT_REENTRY="1")
    result = subprocess.run(["bash", str(source / "deploy/huawei/deploy.sh"), "--apply"],
                            capture_output=True, text=True, env=fake_reentry)
    assert result.returncode != 0
    assert "不匹配" in result.stderr
    assert "frozen-transaction:" not in result.stdout
    frozen_script = release / "deploy/huawei/deployment_transaction.py"
    frozen_script.chmod(0o644)
    frozen_script.write_text("tampered")
    frozen_script.chmod(0o444)
    result = subprocess.run(["bash", str(source / "deploy/huawei/deploy.sh"), "--apply"],
                            capture_output=True, text=True,
                            env=environment)
    assert result.returncode != 0
    assert "已损坏" in result.stderr
    assert "frozen-transaction:" not in result.stdout
