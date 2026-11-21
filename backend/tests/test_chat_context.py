from __future__ import annotations

import io
from types import SimpleNamespace
from uuid import uuid4

import pytest
from flask import g

from app import create_app
from app.api import chat as chat_api
from app.services import chat_context
from app.services.chat_context import (
    AttachmentError,
    extract_attachment_text,
    load_attachment,
    project_context,
    save_attachment,
    with_attachment,
)


def _app(tmp_path, **extra):
    return create_app({"TESTING": True, "CHAT_ATTACHMENT_DIR": tmp_path, **extra})


def _pdf_bytes(text: str) -> bytes:
    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def _docx_bytes() -> bytes:
    from docx import Document

    document = Document()
    document.add_paragraph("Retrieval augmented generation")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "recall"
    table.rows[0].cells[1].text = "0.81"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _xlsx_bytes() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "results"
    sheet.append(["chunk", "recall"])
    sheet.append([512, 0.81])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_attachment_text_is_extracted_from_each_supported_type():
    assert "Graph RAG" in extract_attachment_text("paper.pdf", _pdf_bytes("Graph RAG improves recall"))
    docx_text = extract_attachment_text("notes.docx", _docx_bytes())
    assert "Retrieval augmented generation" in docx_text and "recall | 0.81" in docx_text
    assert "512\t0.81" in extract_attachment_text("results.xlsx", _xlsx_bytes())
    assert extract_attachment_text("data.csv", "a,b\n1,2\n".encode()) == "a\tb\n1\t2"
    assert extract_attachment_text("note.txt", "中文内容".encode("gb18030")) == "中文内容"


def test_attachment_rejects_unsupported_empty_and_broken_files():
    with pytest.raises(AttachmentError) as legacy:
        extract_attachment_text("old.doc", b"binary")
    assert legacy.value.code == "CHAT_ATTACHMENT_TYPE_INVALID"
    with pytest.raises(AttachmentError):
        extract_attachment_text("image.png", b"png")
    with pytest.raises(AttachmentError) as empty:
        extract_attachment_text("blank.txt", b"   \n ")
    assert empty.value.code == "CHAT_ATTACHMENT_EMPTY"
    with pytest.raises(AttachmentError) as broken:
        extract_attachment_text("broken.pdf", b"%PDF-not really")
    assert broken.value.code == "CHAT_ATTACHMENT_UNREADABLE"


def test_saved_attachment_is_truncated_and_private_to_its_owner(tmp_path):
    app = _app(tmp_path, CHAT_ATTACHMENT_MAX_CHARS=10)
    owner, other = uuid4(), uuid4()
    with app.app_context():
        saved = save_attachment(owner, "../../notes.txt", "0123456789abcdef".encode())
        assert saved["fileName"] == "notes.txt"
        assert saved["truncated"] is True and saved["chars"] == 16
        record = load_attachment(owner, saved["id"])
        assert record["text"] == "0123456789"
        assert load_attachment(other, saved["id"]) is None
        assert load_attachment(owner, "../" + saved["id"]) is None
        assert "前面部分" in with_attachment(record, "总结一下")
        assert with_attachment(record, "总结一下").endswith("总结一下")


def test_attachment_size_limit(tmp_path):
    app = _app(tmp_path, CHAT_ATTACHMENT_MAX_BYTES=5)
    with app.app_context(), pytest.raises(AttachmentError) as exc:
        save_attachment(uuid4(), "big.txt", b"123456")
    assert exc.value.status == 413


class _Scalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


def test_project_context_lists_goal_tasks_and_budgeted_documents(tmp_path, monkeypatch):
    app = _app(tmp_path, PROJECT_CONTEXT_MAX_CHARS=1200)
    project = SimpleNamespace(id=uuid4(), name="长文档问答", research_goal="提升召回率", description="")
    tasks = [SimpleNamespace(input_json={"prompt": "检索 RAG 论文"}, task_type="literature_search", status="SUCCEEDED")]
    documents = [
        SimpleNamespace(title="实验记录", current_version=3, content="切块 512 召回率 0.81。" * 200),
        SimpleNamespace(title="综述", current_version=1, content="相关工作"),
    ]
    results = iter([_Scalars(tasks), _Scalars(documents)])
    monkeypatch.setattr(chat_context.db.session, "scalars", lambda query: next(results))
    with app.app_context():
        text = project_context(project)
    assert "项目名称：长文档问答" in text and "研究目标：提升召回率" in text
    assert "检索 RAG 论文（literature_search，已完成）" in text
    assert "《实验记录》（第 3 版）" in text and "……（后略）" in text
    assert "《综述》（第 1 版）\n相关工作" in text
    assert len(text) < 1600


def _prepare(app, monkeypatch, payload, *, access=None, history=()):
    monkeypatch.setattr(chat_api, "ensure_user_api_config", lambda user_id: None)
    monkeypatch.setattr(chat_api, "ensure_token_quota", lambda *args: None)
    monkeypatch.setattr(chat_api, "get_conversation_access", lambda conversation_id, edit=False: access)
    appended = []
    monkeypatch.setattr(chat_api, "append_message", lambda *args: appended.append(args))
    monkeypatch.setattr(chat_api.db.session, "commit", lambda: None)
    monkeypatch.setattr(chat_api.db.session, "scalars", lambda query: _Scalars(list(history)[::-1]))
    monkeypatch.setattr(chat_api, "project_context", lambda project: f"PROJECT:{project.name}")
    with app.test_request_context(json=payload):
        g.current_user = SimpleNamespace(id=app.config["_USER"], role_code="normal_user")
        chat_request, failure = chat_api.prepare_chat_request()
    return chat_request, failure, appended


def test_chat_turn_carries_attachment_text_and_records_it_on_the_message(tmp_path, monkeypatch):
    user = uuid4()
    app = _app(tmp_path, _USER=user)
    with app.app_context():
        saved = save_attachment(user, "paper.txt", "Graph RAG improves recall.".encode())
    conversation = SimpleNamespace(project_id=uuid4())
    project = SimpleNamespace(name="长文档问答")
    chat_request, failure, appended = _prepare(
        app,
        monkeypatch,
        {"prompt": "这篇讲了什么？", "attachment_id": saved["id"], "conversation_id": str(uuid4())},
        access=(conversation, project, "OWNER"),
    )
    assert failure is None
    roles = [item["role"] for item in chat_request.messages]
    assert roles == ["system", "system", "user"]
    assert chat_request.messages[1]["content"] == "PROJECT:长文档问答"
    assert "Graph RAG improves recall." in chat_request.messages[-1]["content"]
    assert chat_request.messages[-1]["content"].endswith("这篇讲了什么？")
    # The stored message keeps what the person typed plus the file reference.
    _conversation_id, role, content, content_json = appended[0]
    assert (role, content) == ("user", "这篇讲了什么？")
    assert content_json["attachment"]["fileName"] == "paper.txt"


def test_follow_up_question_still_sees_the_latest_attachment(tmp_path, monkeypatch):
    user = uuid4()
    app = _app(tmp_path, _USER=user)
    with app.app_context():
        saved = save_attachment(user, "paper.txt", "Graph RAG improves recall.".encode())
    chat_request, failure, _ = _prepare(
        app,
        monkeypatch,
        {
            "prompt": "第二个问题",
            "messages": [
                {"role": "user", "content": "总结一下", "attachment_id": saved["id"]},
                {"role": "assistant", "content": "它提升了召回率。"},
            ],
        },
    )
    assert failure is None
    assert "Graph RAG improves recall." in chat_request.messages[1]["content"]
    assert chat_request.messages[-1] == {"role": "user", "content": "第二个问题"}


def test_unknown_attachment_is_rejected(tmp_path, monkeypatch):
    app = _app(tmp_path, _USER=uuid4())
    _, failure, _ = _prepare(app, monkeypatch, {"prompt": "hi", "attachment_id": "0" * 32})
    response, status = failure
    assert status == 404
    assert response.get_json()["error"]["code"] == "CHAT_ATTACHMENT_NOT_FOUND"
