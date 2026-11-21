from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from flask import g

from app import create_app
from app.api import admin as admin_api
from app.extensions import db


def admin_user():
    return SimpleNamespace(id=uuid4(), role_code="system_admin")


def test_system_dashboard_uses_database_counts(monkeypatch):
    app = create_app({"TESTING": True})
    counts = iter([12, 3, 2, 1, 8, 4])
    monkeypatch.setattr(admin_api, "_count", lambda *_args: next(counts))

    with app.test_request_context():
        g.current_user = admin_user()
        response, status = admin_api.overview()

    assert status == 200
    payload = response.get_json()["data"]
    assert [metric["value"] for metric in payload["metrics"]] == [12, 3, 2, 1]
    assert payload["summary"]["activeAgents"] == 8
    assert payload["summary"]["activeTools"] == 4


def test_system_exceptions_are_web_tasks(monkeypatch):
    app = create_app({"TESTING": True})
    now = datetime.now(UTC)
    failed_task = SimpleNamespace(
        id=uuid4(),
        task_type="paper_reading",
        status="FAILED",
        safe_error_message="模型服务不可用",
        error_code="MODEL_UNAVAILABLE",
        retry_count=2,
        updated_at=now,
        created_at=now,
    )
    monkeypatch.setattr(db.session, "scalars", lambda _query: SimpleNamespace(all=lambda: [failed_task]))

    with app.test_request_context():
        g.current_user = admin_user()
        response, status = admin_api.system_exceptions()

    assert status == 200
    item = response.get_json()["data"]["items"][0]
    assert item["type"] == "paper_reading"
    assert item["message"] == "模型服务不可用"
    assert item["retryCount"] == 2


def test_system_admin_endpoints_reject_normal_users():
    app = create_app({"TESTING": True})
    with app.test_request_context():
        g.current_user = SimpleNamespace(role_code="normal_user")
        response, status = admin_api.system_permissions()

    assert status == 403
    assert response.get_json()["error"]["code"] == "FORBIDDEN"
