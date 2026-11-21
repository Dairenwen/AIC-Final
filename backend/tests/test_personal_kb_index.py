from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app import create_app
from app.services import personal_kb_index
from app.services.personal_kb_index import cosine, index_personal_paper, pdf_pages, split_page


def _pdf(pages: list[str]) -> bytes:
    import fitz

    document = fitz.open()
    for text in pages:
        page = document.new_page()
        page.insert_textbox(fitz.Rect(72, 72, 540, 760), text)
    data = document.tobytes()
    document.close()
    return data


def test_pdf_pages_keep_page_numbers_and_skip_blank_pages():
    data = _pdf(["Retrieval augmented generation for long documents. " * 5, "", "Results show higher recall. " * 5])
    pages = pdf_pages(data)
    assert [number for number, _ in pages] == [1, 3]
    assert "Retrieval augmented generation" in pages[0][1]


def test_split_page_overlaps_long_pages_and_drops_fragments():
    chunks = split_page("a" * 2000)
    assert len(chunks) == 3 and all(len(item) <= personal_kb_index.CHUNK_SIZE for item in chunks)
    assert split_page("short") == []


def test_cosine_similarity():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_index_personal_paper_writes_page_chunks_and_status(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "PERSONAL_KB_UPLOAD_DIR": tmp_path, "KNOWLEDGE_BASE_MILVUS_ENABLED": True})
    owner = uuid4()
    (tmp_path / "u").mkdir()
    (tmp_path / "u" / "p.pdf").write_bytes(_pdf(["Graph based retrieval improves recall. " * 4, "Ablation study on chunk size. " * 4]))
    paper = SimpleNamespace(
        id=uuid4(), owner_user_id=owner, source_type="LOCAL_UPLOAD", object_key="u/p.pdf", metadata_json={"x": 1}
    )
    added, executed, commits = [], [], []
    session = personal_kb_index.db.session
    monkeypatch.setattr(session, "get", lambda model, key: paper)
    monkeypatch.setattr(session, "execute", lambda statement: executed.append(statement))
    monkeypatch.setattr(session, "add", lambda item: added.append(item))
    monkeypatch.setattr(session, "commit", lambda: commits.append(True))
    monkeypatch.setattr(
        "knowledge_base_runtime.backend.service.retrieval_backends.embed_texts",
        lambda texts: [[float(len(text)), 1.0] for text in texts],
    )
    with app.app_context():
        count = index_personal_paper(paper.id)

    assert count == 2 and len(added) == 2 and commits
    assert [item.page_no for item in added] == [1, 2]
    assert [item.chunk_index for item in added] == [0, 1]
    assert all(item.owner_user_id == owner and item.embedding for item in added)
    assert executed, "old chunks of the paper are deleted before re-indexing"
    assert paper.metadata_json["index_status"] == "INDEXED"
    assert paper.metadata_json["index_chunks"] == 2 and paper.metadata_json["index_semantic"] is True
    assert paper.metadata_json["x"] == 1


def test_index_skips_platform_references(monkeypatch):
    app = create_app({"TESTING": True})
    reference = SimpleNamespace(source_type="PLATFORM_REFERENCE", object_key=None)
    monkeypatch.setattr(personal_kb_index.db.session, "get", lambda model, key: reference)
    with app.app_context():
        assert index_personal_paper(uuid4()) == 0
