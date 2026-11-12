"""Recovery must be retryable and must never overwrite user data."""
import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/docker/restore_files.py"
spec = importlib.util.spec_from_file_location("docker_restore_files", SCRIPT)
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


@pytest.fixture
def roots(tmp_path):
    source, base = tmp_path / "source", tmp_path / "data"
    source.mkdir()
    base.mkdir()
    return source, base, base / "result", base / ".state"


def apply(roots):
    source, base, destination, state = roots
    restore.restore_tree(source, destination, base, state)


def test_restore_and_repeat_preserves_contents_and_links(roots):
    source, base, destination, state = roots
    (source / "nested").mkdir()
    (source / "nested" / "paper.pdf").write_bytes(b"pdf contents")
    (source / "link").symlink_to("nested/paper.pdf")
    apply(roots)
    apply(roots)
    assert (destination / "nested" / "paper.pdf").read_bytes() == b"pdf contents"
    assert os.readlink(destination / "link") == "nested/paper.pdf"
    assert list((state / "restore-staging").iterdir()) == []


@pytest.mark.parametrize("existing", [b"user edit", b"other123", b"orig"])
def test_existing_different_user_file_never_overwritten(roots, existing):
    source, base, destination, state = roots
    (source / "paper").write_bytes(b"original")
    destination.mkdir()
    (destination / "paper").write_bytes(existing)
    with pytest.raises(ValueError, match="已存在"):
        apply(roots)
    assert (destination / "paper").read_bytes() == existing


def test_partial_copy_is_not_exposed_and_retry_succeeds(roots, monkeypatch):
    source, base, destination, state = roots
    (source / "paper").write_bytes(b"complete document")
    stream_copy = restore.stream_copy

    def fail_mid_copy(input_file, output_file):
        output_file.write(input_file.read(4))
        raise OSError("interrupted")

    monkeypatch.setattr(restore, "stream_copy", fail_mid_copy)
    with pytest.raises(OSError, match="interrupted"):
        apply(roots)
    assert not (destination / "paper").exists()
    monkeypatch.setattr(restore, "stream_copy", stream_copy)
    apply(roots)
    assert (destination / "paper").read_bytes() == b"complete document"


def test_staging_left_by_killed_process_is_safely_retried(roots):
    source, base, destination, state = roots
    (source / "paper").write_bytes(b"complete document")
    staging = state / "restore-staging"
    staging.mkdir(parents=True)
    name = hashlib.sha256(b"result/paper").hexdigest()
    (staging / name).write_bytes(b"incomplete")
    apply(roots)
    assert (destination / "paper").read_bytes() == b"complete document"
    assert not (staging / name).exists()


def test_stage_hard_link_to_user_file_is_not_truncated(roots):
    source, base, destination, state = roots
    (source / "paper").write_bytes(b"source")
    user_file = base / "user-file"
    user_file.write_bytes(b"valuable")
    staging = state / "restore-staging"
    staging.mkdir(parents=True)
    name = hashlib.sha256(b"result/paper").hexdigest()
    os.link(user_file, staging / name)
    with pytest.raises(ValueError, match="临时文件状态异常"):
        apply(roots)
    assert user_file.read_bytes() == b"valuable"


def test_existing_target_symlink_does_not_write_outside_data(roots, tmp_path):
    source, base, destination, state = roots
    (source / "nested").mkdir()
    (source / "nested" / "paper").write_bytes(b"source")
    destination.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (destination / "nested").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="不是普通目录"):
        apply(roots)
    assert not (external / "paper").exists()


def test_external_source_symlink_is_rejected(roots, tmp_path):
    source, base, destination, state = roots
    external = tmp_path / "external"
    external.write_bytes(b"private")
    (source / "link").symlink_to(external)
    with pytest.raises(ValueError, match="超出来源目录"):
        apply(roots)
    assert not (destination / "link").exists()


def test_existing_restore_verification_is_read_only(roots):
    source, base, destination, state = roots
    (source / "paper").write_bytes(b"original")
    destination.mkdir()
    restored = destination / "paper"
    restored.write_bytes(b"original")
    before = restored.stat().st_mtime_ns
    restore.verify_tree(source, destination, base)
    assert restored.stat().st_mtime_ns == before
    assert not state.exists()
    restored.write_bytes(b"modified")
    with pytest.raises(ValueError, match="已存在"):
        restore.verify_tree(source, destination, base)
    assert restored.read_bytes() == b"modified"
    assert not state.exists()
