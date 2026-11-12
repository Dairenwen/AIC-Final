"""The Docker restore guard must never accept partial or mismatched volumes."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import types

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/docker/database_state.py"
spec = importlib.util.spec_from_file_location("docker_database_state", SCRIPT)
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)


class Result:
    def __init__(self, value):
        self.value = value

    def one(self):
        return self.value

    def all(self):
        return self.value

    def scalar_one(self):
        return self.value


class Connection:
    def __init__(self, identity, tables, schemas):
        self.identity, self.tables, self.schemas = identity, tables, schemas
        self.proof = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query):
        if "pg_control_system" in query:
            return Result(self.identity)
        if "pg_class" in query:
            return Result(list(self.tables))
        if "to_regclass" in query:
            return Result(self.proof is not None)
        if "SELECT token, backup" in query:
            return Result(self.proof)
        return Result(self.schemas)


@pytest.fixture
def run_guard(tmp_path, monkeypatch):
    data = b"".join(f"COPY zhiyan.t{i} (id) FROM stdin;\n".encode() for i in range(100))
    dump = tmp_path / "dump.sql"
    dump.write_bytes(data)
    monkeypatch.setattr(state, "BACKUP_SHA256", hashlib.sha256(data).hexdigest())
    monkeypatch.setenv("DATABASE_URL", "postgresql://not-used")
    tables = {("zhiyan", f"t{i}") for i in range(100)}
    conn = Connection(("original-cluster", "42"), tables, 1)
    module = types.SimpleNamespace(create_engine=lambda *a, **k: types.SimpleNamespace(connect=lambda: conn), text=lambda x: x)
    monkeypatch.setitem(sys.modules, "sqlalchemy", module)

    def run(stage):
        monkeypatch.setattr(sys, "argv", [str(SCRIPT), stage, "--dump", str(dump), "--state-dir", str(tmp_path)])
        return state.main()

    return tmp_path, conn, run


def test_fresh_restore_then_matching_restart(run_guard):
    path, conn, run = run_guard
    conn.schemas, conn.tables = 0, set()
    assert run("before") == 0
    conn.schemas, conn.tables = 1, {("zhiyan", f"t{i}") for i in range(100)}
    assert run("after") == 0
    (path / "db-initialised").touch()
    assert run("before") == 0


@pytest.mark.parametrize("failure", ["empty_db", "partial_db", "other_cluster", "other_database", "missing_binding", "invalid_binding"])
def test_existing_marker_refuses_unsafe_states(run_guard, failure):
    path, conn, run = run_guard
    (path / "db-initialised").touch()
    binding = path / "db-identity.json"
    binding.write_text(json.dumps({"cluster": "original-cluster", "database": "42"}))
    if failure == "empty_db":
        conn.schemas, conn.tables = 0, set()
    elif failure == "partial_db":
        conn.tables.remove(("zhiyan", "t0"))
    elif failure == "other_cluster":
        conn.identity = ("different-cluster", "42")
    elif failure == "other_database":
        conn.identity = ("original-cluster", "43")
    elif failure == "missing_binding":
        binding.unlink()
    else:
        binding.write_text("not json")
    assert run("before") == 1


def test_existing_database_requires_explicit_adoption(run_guard):
    path, conn, run = run_guard
    assert run("before") == 1
    assert run("adopt-existing") == 0
    assert run("before") == 0
    assert not (path / "db-initialised").exists()


def test_adoption_rejects_incomplete_database(run_guard):
    path, conn, run = run_guard
    conn.tables.remove(("zhiyan", "t0"))
    assert run("adopt-existing") == 1
    assert not (path / "db-identity.json").exists()


def test_tampered_dump_rejected(run_guard):
    path, conn, run = run_guard
    (path / "dump.sql").write_bytes(b"changed")
    assert run("before") == 1


def test_committed_restore_recovers_binding_only_with_matching_proof(run_guard):
    path, conn, run = run_guard
    conn.schemas, conn.tables = 0, set()
    assert run("prepare") == 0
    record = json.loads((path / state.JOURNAL).read_text())
    assert (path / state.JOURNAL).stat().st_mode & 0o077 == 0
    conn.schemas, conn.tables = 1, {("zhiyan", f"t{i}") for i in range(100)}
    conn.proof = (record["token"], record["backup"])
    assert run("before") == 0
    assert json.loads((path / "db-identity.json").read_text()) == {
        "cluster": "original-cluster", "database": "42"
    }
    assert run("after") == 0


@pytest.mark.parametrize("failure", ["missing_journal", "wrong_cluster", "missing_proof", "wrong_token", "wrong_backup", "partial_tables"])
def test_interrupted_restore_never_adopts_unproven_db(run_guard, failure):
    path, conn, run = run_guard
    conn.schemas, conn.tables = 0, set()
    assert run("prepare") == 0
    record = json.loads((path / state.JOURNAL).read_text())
    conn.schemas, conn.tables = 1, {("zhiyan", f"t{i}") for i in range(100)}
    conn.proof = (record["token"], record["backup"])
    if failure == "missing_journal":
        (path / state.JOURNAL).unlink()
    elif failure == "wrong_cluster":
        conn.identity = ("other-cluster", "42")
    elif failure == "missing_proof":
        conn.proof = None
    elif failure == "wrong_token":
        conn.proof = ("0" * 64, record["backup"])
    elif failure == "wrong_backup":
        conn.proof = (record["token"], "0" * 64)
    else:
        conn.tables.remove(("zhiyan", "t0"))
    assert run("before") == 1
    assert not (path / "db-identity.json").exists()


def test_prepare_is_retryable_for_same_empty_database_only(run_guard):
    path, conn, run = run_guard
    conn.schemas, conn.tables = 0, set()
    assert run("prepare") == 0
    first = (path / state.JOURNAL).read_bytes()
    assert run("prepare") == 0
    assert (path / state.JOURNAL).read_bytes() == first
    conn.identity = ("other-cluster", "42")
    assert run("prepare") == 1
    conn.schemas = 1
    assert run("prepare") == 1


def test_journal_recovers_crash_between_link_and_unlink(tmp_path, monkeypatch):
    path = tmp_path / state.JOURNAL
    identity = {"cluster": "1", "database": "2"}
    real_unlink = state.os.unlink

    def crash_after_publish(name, *args, **kwargs):
        if Path(name).name.startswith(".db-restore-journal-"):
            raise SystemExit("simulated power loss")
        return real_unlink(name, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(state.os, "unlink", crash_after_publish)
        with pytest.raises(SystemExit, match="simulated power loss"):
            state.prepare_journal(path, identity)
    assert path.stat().st_nlink == 2
    record = state.read_journal(path, identity)
    assert record["cluster"] == "1"
    assert path.stat().st_nlink == 1
    assert not list(tmp_path.glob(".db-restore-journal-*"))


def test_journal_rejects_unrecognized_extra_hard_link(tmp_path):
    path = tmp_path / state.JOURNAL
    identity = {"cluster": "1", "database": "2"}
    state.prepare_journal(path, identity)
    os.link(path, tmp_path / "unrelated-link")
    with pytest.raises(ValueError, match="硬链接"):
        state.read_journal(path, identity)
