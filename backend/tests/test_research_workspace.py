from types import SimpleNamespace
from uuid import uuid4

from flask import g

from app import create_app
from app.api.projects import append_message, get_project_access, serialize_message
from app.extensions import db
from app.api.workspace import AGENT_DISPLAY_ORDER


def test_project_access_isolated_by_membership(monkeypatch):
    app = create_app({"TESTING": True})
    project_id = uuid4()
    project = SimpleNamespace(id=project_id, status="ACTIVE")
    monkeypatch.setattr(db.session, "get", lambda _model, _id: project)

    with app.test_request_context():
        g.current_user = SimpleNamespace(id=uuid4(), role_code="normal_user")
        monkeypatch.setattr(db.session, "scalar", lambda _query: None)
        assert get_project_access(project_id) is None


def test_viewer_cannot_edit_project(monkeypatch):
    app = create_app({"TESTING": True})
    project_id = uuid4()
    project = SimpleNamespace(id=project_id, status="ACTIVE")
    membership = SimpleNamespace(role="VIEWER")
    monkeypatch.setattr(db.session, "get", lambda _model, _id: project)
    monkeypatch.setattr(db.session, "scalar", lambda _query: membership)

    with app.test_request_context():
        g.current_user = SimpleNamespace(id=uuid4(), role_code="normal_user")
        assert get_project_access(project_id) == (project, "VIEWER")
        assert get_project_access(project_id, edit=True) is None


def test_message_sequence_and_api_role_are_stable(monkeypatch):
    conversation_id = uuid4()
    added = []
    monkeypatch.setattr(db.session, "scalar", lambda _query: 4)
    monkeypatch.setattr(db.session, "add", added.append)

    message = append_message(conversation_id, "assistant", "研究建议")
    message.id = uuid4()
    message.created_at = None

    assert message.sequence == 5
    assert message.role == "ASSISTANT"
    assert serialize_message(message)["role"] == "assistant"
    assert added == [message]


def test_patent_drafting_is_eighth_in_agent_display_order():
    assert AGENT_DISPLAY_ORDER["patent_drafting"] == 8
    ordered = [code for code, _ in sorted(AGENT_DISPLAY_ORDER.items(), key=lambda item: item[1])]
    assert ordered[7] == "patent_drafting"
