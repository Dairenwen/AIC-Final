import importlib.util
from pathlib import Path
import tarfile

import pytest


@pytest.fixture
def package(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[2] / "scripts/package_delivery.py"
    spec = importlib.util.spec_from_file_location("test_package_delivery_module", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    return module


def test_complete_archive_includes_private_data_code_and_safe_model_links(package, tmp_path):
    code = tmp_path / "backend"
    code.mkdir()
    (code / "app.py").write_text("application")
    (code / ".env.production").write_text("secret")
    data = tmp_path / "补充"
    model = data / "docling_hf"
    blob = model / "hub/models/blobs/model"
    blob.parent.mkdir(parents=True)
    blob.write_text("weights")
    snapshot = model / "hub/models/snapshots/revision"
    snapshot.mkdir(parents=True)
    (snapshot / "weights").symlink_to("../../blobs/model")
    (data / "backup.sql").write_text("private data")
    literature = tmp_path / "文献/arxiv"
    literature.mkdir(parents=True)
    (literature / "paper.pdf").write_text("paper")
    output = tmp_path / "out.tar"
    package.create_archive(output, [code, data, literature], docling_root=model)
    assert output.stat().st_mode & 0o777 == 0o600
    with tarfile.open(output) as archive:
        members = {member.name: member for member in archive.getmembers()}
        assert {"backend/app.py", "补充/backup.sql", "文献/arxiv/paper.pdf",
                "补充/docling_hf/hub/models/blobs/model"} <= members.keys()
        assert "backend/.env.production" not in members
        entry = members["补充/docling_hf/hub/models/snapshots/revision/weights"]
        assert entry.issym()
        assert entry.linkname == "../../blobs/model"
        assert archive.extractfile("补充/docling_hf/hub/models/blobs/model").read() == b"weights"


@pytest.mark.parametrize("target", ["/etc/passwd", "../../../../../outside", "missing"])
def test_model_cache_rejects_unsafe_or_broken_link(package, tmp_path, target):
    model = tmp_path / "补充/docling_hf"
    hub = model / "hub"
    hub.mkdir(parents=True)
    (hub / "link").symlink_to(target)
    with pytest.raises(ValueError, match="符号链接"):
        list(package.docling_entries(model))


def test_model_cache_rejects_link_to_excluded_secret(package, tmp_path):
    model = tmp_path / "补充/docling_hf"
    hub = model / "hub"
    hub.mkdir(parents=True)
    (hub / ".env.production").write_text("secret")
    (hub / "link").symlink_to(".env.production")
    with pytest.raises(ValueError, match="排除文件"):
        list(package.docling_entries(model))


def test_source_symlink_directory_is_rejected(package, tmp_path):
    code = tmp_path / "backend"
    code.mkdir()
    (code / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RuntimeError, match="符号链接目录"):
        list(package.files(code))


def test_missing_model_does_not_create_output(package, tmp_path):
    code = tmp_path / "backend"
    code.mkdir()
    output = tmp_path / "out.tar"
    with pytest.raises(FileNotFoundError):
        package.create_archive(output, [code], docling_root=tmp_path / "missing")
    assert not output.exists()
