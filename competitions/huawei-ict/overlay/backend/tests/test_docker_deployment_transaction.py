"""Production retries must not remove data or silently upgrade a running version."""
import copy
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import types

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/huawei/deployment_transaction.py"
spec = importlib.util.spec_from_file_location("deployment_transaction", SCRIPT)
transaction = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transaction)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    here = tmp_path / "deploy/huawei"
    here.mkdir(parents=True)
    fake = types.SimpleNamespace(
        version="v1", config="config-hash", existing=False, containers={}, failure=None, calls=[],
        images={"backend-image": "sha:original"},
    )
    services = ["backend", "web"]

    def fingerprint(root, compose, release):
        source_version = "v1" if release.name == "a" * 64 else "v2"
        return {"source_sha256": source_version, "configuration_sha256": fake.config}

    def snapshot(compose):
        return {"services": services, "containers": copy.deepcopy(fake.containers)}

    def run(command, **kwargs):
        fake.calls.append(command)
        if "build" in command:
            event = "build"
        elif "up" in command:
            event = "up"
        elif any("verify.py" in part for part in command):
            event = "verify"
        elif command[0] == "curl":
            event = "https"
        elif any("bootstrap_search_index.py" in part for part in command):
            event = "index"
        else:
            event = "inspect"
        if fake.failure == event:
            raise subprocess.CalledProcessError(1, command)
        if event == "up":
            fake.containers = {
                name: {"config": name + "-hash", "container": name + "-id",
                       "image": "sha:original", "reference": "backend-image"}
                for name in services
            }
        return types.SimpleNamespace(returncode=0, stdout="sha:original\n")

    monkeypatch.setattr(transaction, "effective_fingerprint", fingerprint)
    monkeypatch.setattr(transaction, "compose_command", lambda root, release: ["docker", "compose"])
    monkeypatch.setattr(transaction.release_snapshot, "source_manifest", lambda root: {"version": fake.version})
    monkeypatch.setattr(transaction.release_snapshot, "release_id", lambda manifest: ("a" if manifest["version"] == "v1" else "b") * 64)
    monkeypatch.setattr(transaction.release_snapshot, "create_release", lambda root, releases: (releases / (("a" if fake.version == "v1" else "b") * 64), ("a" if fake.version == "v1" else "b") * 64))
    monkeypatch.setattr(transaction.release_snapshot, "verify_release", lambda release, identity: None)
    monkeypatch.setattr(transaction, "container_snapshot", snapshot)
    monkeypatch.setattr(transaction, "built_image_snapshot", lambda compose: dict(fake.images))
    monkeypatch.setattr(transaction, "unmanaged_resources", lambda compose: fake.existing)
    monkeypatch.setattr(transaction, "run", run)
    fake.root = tmp_path
    fake.state = here / transaction.STATE_NAME
    fake.deploy = lambda **kw: transaction.deploy(tmp_path, "example.com", **kw)
    fake.load = lambda: transaction.load_state(fake.state)
    return fake


@pytest.mark.parametrize("failure", ["build", "up", "verify", "https", "index"])
def test_failure_preserves_initial_state_and_retry_succeeds(harness, failure):
    harness.failure = failure
    with pytest.raises(subprocess.CalledProcessError):
        harness.deploy()
    assert harness.load()["status"] == "initializing"
    harness.failure = None
    harness.calls.clear()
    harness.deploy()
    assert harness.load()["status"] == "started"
    if failure != "build":
        assert all("build" not in command for command in harness.calls)
    assert all("down" not in command and "rm" not in command for command in harness.calls)


def test_repeat_started_uses_same_containers_without_build_or_recreation(harness):
    harness.deploy()
    old = copy.deepcopy(harness.load()["containers"])
    harness.calls.clear()
    harness.deploy()
    assert harness.load()["containers"] == old
    command = next(c for c in harness.calls if "up" in c)
    assert "--no-build" in command and "--no-recreate" in command
    assert not any("build" in c for c in harness.calls)


def test_unknown_existing_resource_is_never_adopted(harness):
    harness.existing = True
    with pytest.raises(RuntimeError, match="没有登记"):
        harness.deploy()
    assert not harness.state.exists()
    assert not harness.calls


def test_initializing_change_needs_explicit_confirmation(harness):
    harness.failure = "verify"
    with pytest.raises(subprocess.CalledProcessError):
        harness.deploy()
    harness.version = "v2"
    harness.failure = None
    with pytest.raises(RuntimeError, match="已变化"):
        harness.deploy()
    harness.deploy(accept_initial_changes=True)
    assert harness.load()["status"] == "started"
    assert harness.load()["fingerprint"]["source_sha256"] == "v2"


def test_successful_version_restarts_from_snapshot_even_if_source_changed(harness):
    harness.deploy()
    harness.version = "v2"
    harness.deploy()
    assert harness.load()["fingerprint"]["source_sha256"] == "v1"
    with pytest.raises(RuntimeError, match="不接受"):
        harness.deploy(accept_initial_changes=True)


def test_successful_version_rejects_changed_private_configuration(harness):
    harness.deploy()
    harness.config = "changed"
    for confirm in (False, True):
        with pytest.raises(RuntimeError, match="已变化"):
            harness.deploy(accept_initial_changes=confirm)


def test_changed_or_missing_container_refused(harness):
    harness.deploy()
    harness.containers["backend"]["image"] = "sha:changed"
    harness.calls.clear()
    with pytest.raises(RuntimeError, match="不一致"):
        harness.deploy()
    assert not harness.calls


def test_replaced_built_image_rejected_on_initial_retry(harness):
    harness.failure = "verify"
    with pytest.raises(subprocess.CalledProcessError):
        harness.deploy()
    harness.failure = None
    harness.images["backend-image"] = "sha:replaced"
    with pytest.raises(RuntimeError, match="镜像已被替换"):
        harness.deploy()


def test_cannot_start_two_deployments_concurrently(harness):
    lock_path = harness.root / "deploy/huawei" / transaction.LOCK_NAME
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="正在执行"):
            harness.deploy()
    assert not harness.state.exists()


def test_full_acceptance_is_not_recorded(harness, capsys):
    harness.deploy()
    assert "不代表全部业务" in capsys.readouterr().out
    assert "acceptance" not in harness.load()
    command = next(c for c in harness.calls if c[0] == "curl")
    assert command[command.index("--output") + 1] == "/dev/null"


@pytest.mark.parametrize("failure", [None, "verify"])
def test_deployment_restores_inherited_environment(harness, monkeypatch, failure):
    monkeypatch.setenv("ZHIYAN_DEPLOY_ROOT", "inherited-other-project")
    monkeypatch.delenv("ZHIYAN_ENV_FILE", raising=False)
    monkeypatch.delenv("ZHIYAN_RELEASE_DIR", raising=False)
    harness.failure = failure
    if failure:
        with pytest.raises(subprocess.CalledProcessError):
            harness.deploy()
    else:
        harness.deploy()
    assert os.environ["ZHIYAN_DEPLOY_ROOT"] == "inherited-other-project"
    assert "ZHIYAN_ENV_FILE" not in os.environ
    assert "ZHIYAN_RELEASE_DIR" not in os.environ


def test_fingerprint_detects_source_config_and_data_changes_without_including_state(tmp_path):
    for path in ("backend", "frontend", "scripts", "deploy/huawei/tls", "文献/arxiv", "补充", ".codeartsdoer"):
        (tmp_path / path).mkdir(parents=True, exist_ok=True)
    project_root = SCRIPT.parents[2]
    shutil.copyfile(project_root / "scripts/package_delivery.py", tmp_path / "scripts/package_delivery.py")
    (tmp_path / ".dockerignore").write_text("ignore")
    (tmp_path / "backend/main.py").write_text("v1")
    (tmp_path / "deploy/huawei/.env.production").write_text("SECRET=private-value")
    (tmp_path / "deploy/huawei/tls/fullchain.pem").write_text("cert")
    (tmp_path / "deploy/huawei/tls/privkey.pem").write_text("private key")
    (tmp_path / "补充/zhiyan_2026-10-09_11-47-01_pgsql_data.sql").write_text("dump")
    (tmp_path / "文献/arxiv/paper.pdf").write_text("paper")
    before = transaction.fingerprint(tmp_path)
    (tmp_path / "deploy/huawei/.env.deploy-state.json").write_text("state")
    assert transaction.fingerprint(tmp_path) == before
    (tmp_path / "deploy/huawei/.env.production").write_text("SECRET=changed-value")
    config_changed = transaction.fingerprint(tmp_path)
    assert config_changed["source_sha256"] == before["source_sha256"]
    assert config_changed["configuration_sha256"] != before["configuration_sha256"]
    (tmp_path / "backend/main.py").write_text("v2")
    source_changed = transaction.fingerprint(tmp_path)
    assert source_changed["source_sha256"] != config_changed["source_sha256"]
    (tmp_path / ".codeartsdoer/skill.txt").write_text("skill change")
    skill_changed = transaction.fingerprint(tmp_path)
    assert skill_changed["source_sha256"] != source_changed["source_sha256"]
    (tmp_path / "文献/arxiv/paper.pdf").write_text("changed paper contents")
    assert transaction.fingerprint(tmp_path)["asset_metadata_sha256"] != skill_changed["asset_metadata_sha256"]


def test_effective_compose_configuration_is_hashed_but_not_stored(monkeypatch, tmp_path):
    monkeypatch.setattr(transaction, "fingerprint", lambda root, release: {"source_sha256": "source"})
    rendered = '{"secret":"private-value"}'
    monkeypatch.setattr(transaction, "run", lambda *a, **k: types.SimpleNamespace(stdout=rendered))
    result = transaction.effective_fingerprint(tmp_path, ["docker", "compose"], tmp_path)
    assert "private-value" not in str(result)
    assert len(result["effective_compose_sha256"]) == 64


def test_volume_listing_failure_cannot_be_treated_as_empty(monkeypatch):
    def fail_on_volumes(command, **kwargs):
        if "volume" in command:
            raise subprocess.CalledProcessError(1, command)
        return types.SimpleNamespace(stdout="", returncode=0)
    monkeypatch.setattr(transaction, "run", fail_on_volumes)
    with pytest.raises(subprocess.CalledProcessError):
        transaction.unmanaged_resources(["docker", "compose"])


def test_unmanaged_existing_data_volume_blocks_initialization(monkeypatch):
    def output(command, **kwargs):
        if "volume" in command:
            return types.SimpleNamespace(stdout="zhiyan-production_zhiyan-data\n", returncode=0)
        return types.SimpleNamespace(stdout="", returncode=0)
    monkeypatch.setattr(transaction, "run", output)
    assert transaction.unmanaged_resources(["docker", "compose"])


def test_source_change_during_build_does_not_change_frozen_snapshot(harness, monkeypatch):
    original = transaction.run
    def change_during_build(command, **kwargs):
        result = original(command, **kwargs)
        if "build" in command:
            harness.version = "edited-during-build"
        return result
    monkeypatch.setattr(transaction, "run", change_during_build)
    harness.deploy()
    assert harness.load()["status"] == "started"
    assert harness.load()["fingerprint"]["source_sha256"] == "v1"


def test_incomplete_service_snapshot_never_records_started(harness, monkeypatch):
    original = transaction.container_snapshot
    def incomplete(compose):
        result = original(compose)
        result["containers"].pop("web", None)
        return result
    monkeypatch.setattr(transaction, "container_snapshot", incomplete)
    with pytest.raises(RuntimeError, match="容器不完整"):
        harness.deploy()
    assert harness.load()["status"] == "initializing"


@pytest.mark.skipif(not shutil.which("docker"), reason="Docker Compose is unavailable")
def test_production_compose_uses_its_own_image_even_if_local_image_is_overridden(tmp_path):
    project = SCRIPT.parents[2]
    env_file = tmp_path / "test.env"
    env_file.write_text("POSTGRES_PASSWORD=dummyvalue\nELASTICSEARCH_PASSWORD=dummyvalue_1234567890\nPUBLIC_ORIGIN=https://example.org\n")
    env = dict(os.environ, ZHIYAN_ENV_FILE=str(env_file), ZHIYAN_BACKEND_IMAGE="other-project:latest", ZHIYAN_RELEASE_DIR=str(project), ZHIYAN_DEPLOY_ROOT=str(project))
    command = ["docker", "compose", "--project-name", "zhiyan-production", "--env-file", str(env_file),
               "-f", str(project / "deploy/docker/docker-compose.yml"),
               "-f", str(project / "deploy/huawei/docker-production.yml"), "config", "--format", "json"]
    result = subprocess.run(command, check=True, capture_output=True, text=True, env=env)
    services = json.loads(result.stdout)["services"]
    for service in ("backend", "mermaid", "mermaid-init"):
        assert services[service]["image"] == "zhiyan-production-backend:release"
    web_health = " ".join(services["web"]["healthcheck"]["test"])
    nginx = (project / "deploy/huawei/nginx-docker-production.conf").read_text()
    assert "listen 80 ssl;" in nginx
    assert "https://127.0.0.1:80/" in web_health
    mounts = {item["target"]: item["source"] for item in services["backend"]["volumes"]}
    assert mounts["/srv/zhiyan"] == str(project)
    assert mounts["/restore"] == str(project / "补充")
    assert mounts["/data/knowledge-base/reference-pdfs"] == str(project / "文献/arxiv")
