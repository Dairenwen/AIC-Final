from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from flask import g

from app import create_app
from app.api.academic_space import (
    extract_arxiv_id,
    inspect_pdf_identity,
    local_object_path,
    normalize_paper_title,
    owned_folder,
    owned_paper,
    parse_platform_authors,
    serialize_folder,
    serialize_paper,
    upload_personal_paper,
)
from app.api import academic_space as academic_space_api
from app.extensions import db
from app.models import PersonalKnowledgeFolder, PersonalKnowledgePaper


def test_platform_author_json_is_normalized():
    assert parse_platform_authors('["Li Ming", "Wang Wei"]') == ["Li Ming", "Wang Wei"]
    assert parse_platform_authors("Li Ming; Wang Wei") == ["Li Ming", "Wang Wei"]
    assert parse_platform_authors("[]") == []


def test_personal_knowledge_queries_are_scoped_to_current_user(monkeypatch):
    app = create_app({"TESTING": True})
    captured = []
    monkeypatch.setattr(db.session, "scalar", lambda query: captured.append(str(query)) or None)
    with app.test_request_context():
        g.current_user = SimpleNamespace(id=uuid4())
        assert owned_folder(uuid4()) is None
        assert owned_paper(uuid4()) is None
    assert len(captured) == 2
    assert all("owner_user_id" in statement for statement in captured)


def test_local_object_path_cannot_escape_user_storage(tmp_path):
    app = create_app({"TESTING": True, "PERSONAL_KB_UPLOAD_DIR": tmp_path})
    with app.app_context():
        safe = SimpleNamespace(object_key="user/paper.pdf")
        escaped = SimpleNamespace(object_key="../../outside.pdf")
        assert local_object_path(safe) == (tmp_path / "user" / "paper.pdf").resolve()
        assert local_object_path(escaped) is None


def test_personal_knowledge_serializers_keep_source_contract():
    folder = SimpleNamespace(
        id=uuid4(), parent_folder_id=None, name="RAG", description=None,
        color="#47745b", created_at=None, updated_at=None,
    )
    paper = SimpleNamespace(
        id=uuid4(), folder_id=folder.id, source_type="PLATFORM_REFERENCE",
        platform_paper_id="98169", title="Dynamic RAG", authors=["Li Ming"],
        abstract=None, publish_venue="ACL", publish_year=2026, source_url=None,
        original_file_name=None, file_size=None, metadata_json={"parse_status": 3}, created_at=None,
    )
    assert serialize_folder(folder, 3)["paper_count"] == 3
    result = serialize_paper(paper)
    assert result["source_type"] == "PLATFORM_REFERENCE"
    assert result["platform_paper_id"] == "98169"


def test_academic_space_tables_have_user_and_folder_foreign_keys():
    folder_fks = {item.target_fullname for item in PersonalKnowledgeFolder.__table__.foreign_keys}
    paper_fks = {item.target_fullname for item in PersonalKnowledgePaper.__table__.foreign_keys}
    assert folder_fks == {"zhiyan.users.id", "zhiyan.personal_knowledge_folders.id"}
    assert paper_fks == {"zhiyan.users.id", "zhiyan.personal_knowledge_folders.id"}


def test_pdf_identity_extracts_arxiv_id_and_normalizes_title():
    identity = inspect_pdf_identity("1806.08730v2.pdf", b"%PDF-1.4\nfixture")
    assert identity["arxiv_id"] == "1806.08730"
    assert len(identity["file_sha256"]) == 64
    assert extract_arxiv_id("https://arxiv.org/pdf/1411.5878.pdf") == "1411.5878"
    assert normalize_paper_title("The Natural-Language Decathlon") == "thenaturallanguagedecathlon"


def test_upload_duplicate_returns_platform_candidate_without_writing_file(tmp_path, monkeypatch):
    app = create_app({
        "TESTING": True,
        "PERSONAL_KB_UPLOAD_DIR": tmp_path,
        "PERSONAL_KB_UPLOAD_MAX_BYTES": 1024 * 1024,
    })
    platform_row = {
        "id": "1806.08730",
        "title": "The Natural Language Decathlon",
        "author": '["Bryan McCann"]',
        "abstract": "Multitask learning as question answering.",
        "publish_venue": "ACL",
        "publish_year": 2018,
        "source_url": "https://arxiv.org/pdf/1806.08730.pdf",
        "source": "crawler",
        "parse_status": 1,
        "citation_count": 10,
    }
    monkeypatch.setattr(academic_space_api, "owned_folder", lambda _folder_id: SimpleNamespace(id=uuid4()))
    monkeypatch.setattr(
        academic_space_api,
        "find_platform_duplicate",
        lambda identity: (platform_row, "ARXIV_ID") if identity["arxiv_id"] == "1806.08730" else None,
    )

    with app.test_request_context(
        method="POST",
        data={"folder_id": str(uuid4()), "file": (BytesIO(b"%PDF-1.4\nfixture"), "1806.08730.pdf")},
        content_type="multipart/form-data",
    ):
        g.current_user = SimpleNamespace(id=uuid4())
        response, status = upload_personal_paper()

    payload = response.get_json()["data"]
    assert status == 200
    assert payload["status"] == "DUPLICATE_FOUND"
    assert payload["match_reason"] == "ARXIV_ID"
    assert payload["platform_paper"]["id"] == "1806.08730"
    assert list(tmp_path.rglob("*.pdf")) == []
