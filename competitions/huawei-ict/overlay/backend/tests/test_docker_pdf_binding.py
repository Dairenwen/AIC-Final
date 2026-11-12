"""Only verified new restores may receive exact, non-overwriting PDF bindings."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/docker/bind_restored_pdfs.py"
spec = importlib.util.spec_from_file_location("docker_pdf_binding", SCRIPT)
binder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(binder)


class Result:
    def __init__(self, value=None, rowcount=0):
        self.value, self.rowcount = value, rowcount

    def scalar_one(self):
        return self.value

    def one(self):
        return self.value

    def one_or_none(self):
        return self.value

    def all(self):
        return self.value


class Connection:
    def __init__(self, proof=True):
        self.proof = proof
        self.completed = None
        self.rows = {"exact": None, "existing": "/original.pdf", "absent": "", "version": None}

    def execute(self, query, params=None):
        query = str(query)
        if "pg_advisory_xact_lock" in query or "CREATE TABLE" in query:
            return Result()
        if "to_regclass" in query:
            return Result(self.proof)
        if "pg_control_system" in query:
            return Result(("1", "2"))
        if "SELECT token, matched_count" in query:
            return Result(self.completed)
        if "SELECT id, minio_pdf_key" in query:
            return Result([(key, value) for key, value in self.rows.items() if not (value or "").strip()])
        if "UPDATE knowledge_base.papers" in query:
            key = params["paper_id"]
            if not (self.rows[key] or "").strip():
                self.rows[key] = params["key"]
                return Result(rowcount=1)
            return Result(rowcount=0)
        if "INSERT INTO __zhiyan_deploy.pdf_binding_state" in query:
            self.completed = (params["token"], params["matched"])
            return Result()
        raise AssertionError(query)


class Engine:
    def __init__(self, connection):
        self.connection = connection

    def begin(self):
        class Context:
            def __enter__(self):
                return self.connection

            def __exit__(self, *args):
                pass
        context = Context()
        context.connection = self.connection
        return context


@pytest.fixture
def verified(tmp_path, monkeypatch):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    (pdf_dir / "exact.pdf").write_bytes(b"pdf")
    (pdf_dir / "existing.pdf").write_bytes(b"pdf")
    (pdf_dir / "versionv2.pdf").write_bytes(b"pdf")
    (pdf_dir / "version.pdf").symlink_to("versionv2.pdf")
    (tmp_path / binder.JOURNAL).write_text("test journal")
    monkeypatch.setattr(binder, "read_journal", lambda *_: {"token": "a" * 64})
    monkeypatch.setattr(binder, "verify_committed_restore", lambda *_: None)
    return tmp_path, pdf_dir


def test_only_exact_existing_empty_keys_are_bound_once(verified):
    state, pdfs = verified
    conn = Connection()
    assert binder.bind(Engine(conn), pdfs, state) == ("bound", 1)
    assert conn.rows == {"exact": "/pdfs/exact.pdf", "existing": "/original.pdf", "absent": "", "version": None}
    conn.rows["exact"] = None  # later deliberate user change must be preserved
    assert binder.bind(Engine(conn), pdfs, state) == ("completed", 1)
    assert conn.rows["exact"] is None


def test_existing_database_without_restore_proof_is_untouched(tmp_path):
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    conn = Connection(proof=False)
    assert binder.bind(Engine(conn), pdfs, tmp_path) == ("existing", 0)
    assert conn.completed is None


def test_incomplete_proof_fails_closed(verified):
    state, pdfs = verified
    conn = Connection(proof=False)
    with pytest.raises(ValueError, match="恢复凭据"):
        binder.bind(Engine(conn), pdfs, state)
    assert conn.completed is None
