"""Historical PDFs stay read-only; uploads cannot overwrite other papers."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import sqlite3

import pytest

from knowledge_base_runtime.backend.service import metadata, pdf_storage, uploads


@pytest.fixture
def locations(tmp_path, monkeypatch):
    writable, reference = tmp_path / "pdfs", tmp_path / "reference-pdfs"
    writable.mkdir()
    reference.mkdir()
    monkeypatch.setattr(pdf_storage, "PDF_DIR", writable)
    monkeypatch.setattr(pdf_storage, "REFERENCE_PDF_DIR", reference)
    return writable, reference


def test_history_and_new_uploads_resolve_without_cross_directory_overwrite(locations, tmp_path):
    writable, reference = locations
    (reference / "old.pdf").write_bytes(b"old")
    source = tmp_path / "incoming"
    source.write_bytes(b"new")
    assert pdf_storage.find_pdf("/pdfs/old.pdf").read_bytes() == b"old"
    published = pdf_storage.store_pdf(source, "新论文")
    assert published.path.read_bytes() == b"new"
    published.close()
    assert pdf_storage.find_pdf("/pdfs/新论文.pdf").read_bytes() == b"new"
    with pytest.raises(ValueError, match="不能覆盖"):
        pdf_storage.store_pdf(source, "old")
    with pytest.raises(ValueError, match="不能覆盖"):
        pdf_storage.store_pdf(source, "新论文")
    assert (reference / "old.pdf").read_bytes() == b"old"
    assert (writable / "新论文.pdf").read_bytes() == b"new"


@pytest.mark.parametrize("paper_id", ["..", "../outside", "/absolute", "a/b", r"a\b", ".hidden", "bad\x00name", "x" * 252])
def test_unsafe_paper_ids_rejected_before_write(locations, tmp_path, paper_id):
    source = tmp_path / "incoming"
    source.write_bytes(b"new")
    with pytest.raises(ValueError, match="安全"):
        pdf_storage.store_pdf(source, paper_id)
    assert not list(locations[0].iterdir())


def test_conflicting_names_and_symlinks_are_not_silently_read(locations, tmp_path):
    writable, reference = locations
    (writable / "same.pdf").write_bytes(b"new")
    (reference / "same.pdf").write_bytes(b"old")
    with pytest.raises(ValueError, match="同名"):
        pdf_storage.find_pdf("/pdfs/same.pdf")
    (reference / "link.pdf").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="符号链接"):
        pdf_storage.find_pdf("/pdfs/link.pdf")


def test_cleanup_never_removes_a_file_replaced_by_someone_else(locations, tmp_path):
    source = tmp_path / "incoming"
    source.write_bytes(b"first")
    published = pdf_storage.store_pdf(source, "paper")
    published.path.unlink()
    published.path.write_bytes(b"someone else's file")
    pdf_storage.remove_own_pdf(published)
    assert published.path.read_bytes() == b"someone else's file"
    published.close()


def sqlite_db(path):
    @contextmanager
    def get_db():
        connection = sqlite3.connect(path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
    return get_db


def test_another_user_cannot_confirm_or_modify_upload_task(tmp_path, monkeypatch):
    db_path = tmp_path / "db.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE upload_tasks(task_id TEXT, user_id TEXT, status TEXT, updated_at TEXT)")
        db.execute("INSERT INTO upload_tasks VALUES ('task-1', 'owner', 'Awaiting Confirm', '')")
    monkeypatch.setattr(uploads, "get_db", sqlite_db(db_path))
    with pytest.raises(ValueError, match="not found"):
        uploads.confirm_upload({"task_id": "task-1"}, "other-user")
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT status FROM upload_tasks").fetchone()[0] == "Awaiting Confirm"


def test_duplicate_id_and_concurrent_inserts_never_publish_second_file(tmp_path, monkeypatch):
    db_path = tmp_path / "db.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE papers (id TEXT PRIMARY KEY, title TEXT)")
    monkeypatch.setattr(metadata, "get_db", sqlite_db(db_path))
    monkeypatch.setattr(metadata, "PAPER_FIELDS", ["id", "title"])
    monkeypatch.setattr(metadata, "normalize_paper", lambda item: item)
    monkeypatch.setattr(metadata, "_upsert_search_index", lambda *_: None)
    monkeypatch.setattr(metadata, "index_paper_elasticsearch", lambda *_: None)
    monkeypatch.setattr(metadata, "record_audit_log", lambda *args, **kwargs: None)
    published = []

    def attempt(i):
        try:
            metadata.upsert_user_paper({"id": "same", "title": str(i)}, "owner",
                                       publish_file=lambda: published.append(i))
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (1, 2)))
    assert sorted(results) == [False, True]
    assert len(published) == 1
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM papers").fetchone()[0] == 1


def test_precommit_failure_cleans_only_this_upload(locations, tmp_path, monkeypatch):
    db_path = tmp_path / "db.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE papers (id TEXT PRIMARY KEY, title TEXT)")
    monkeypatch.setattr(metadata, "get_db", sqlite_db(db_path))
    monkeypatch.setattr(metadata, "PAPER_FIELDS", ["id", "title"])
    monkeypatch.setattr(metadata, "normalize_paper", lambda item: item)
    def fail_index(*_):
        raise RuntimeError("simulated failure")
    monkeypatch.setattr(metadata, "_upsert_search_index", fail_index)
    source = tmp_path / "incoming"
    source.write_bytes(b"new")
    published = []
    def publish():
        item = pdf_storage.store_pdf(source, "paper", "upload_test.pdf")
        published.append(item)
        return item
    try:
        with pytest.raises(RuntimeError, match="simulated"):
            metadata.upsert_user_paper({"id": "paper", "title": "Paper"}, "owner",
                publish_file=publish, cleanup_file=pdf_storage.remove_own_pdf)
    finally:
        for item in published:
            item.close()
    assert not (locations[0] / "upload_test.pdf").exists()
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM papers").fetchone()[0] == 0
