"""Long model/stream waits must not monopolize the small cloud DB pool."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from flask import g
from sqlalchemy import text, update

from app.api.chat import chat
from app.api.tasks import task_events
from app.extensions import db
from app.llm import run_openai_compatible_chat
from app.models import Role, Task, TaskEvent
from app.services.platform_model_runtime import get_platform_model_runtime
from test_huawei_migration import database_app, database_test


def test_sse_rejects_invalid_resume_sequence(monkeypatch):
    from flask import Flask
    monkeypatch.setattr("app.api.tasks.find_task", lambda _: SimpleNamespace(id=uuid4()))
    with Flask("invalid-event-header").test_request_context(headers={"Last-Event-ID": "not-a-number"}):
        response, status = task_events(str(uuid4()))
        assert status == 400
        assert response.get_json()["error"]["code"] == "EVENT_ID_INVALID"


@pytest.fixture
def small_pool_app():
    app = database_app({
        "SQLALCHEMY_ENGINE_OPTIONS": {
            "pool_size": 1, "max_overflow": 0, "pool_timeout": 0.2,
        },
        "QWEN_DPO_ENV_OVERRIDE": False,
    })
    # This fixture must also work when run before the bootstrap test module.
    # database_app() restricts it to an explicitly selected isolated test DB.
    with app.app_context():
        db.session.execute(text('CREATE SCHEMA IF NOT EXISTS zhiyan'))
        db.session.commit()
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


@database_test
@pytest.mark.parametrize("terminal", ["SUCCEEDED", "WAITING_INPUT"])
def test_sse_releases_connection_before_yield_and_preserves_resume(small_pool_app, monkeypatch, terminal):
    app = small_pool_app
    owner, task_id = uuid4(), uuid4()
    with app.app_context():
        db.session.add(Task(id=task_id, user_id=owner, task_type="TEST", status="RUNNING"))
        db.session.flush()
        db.session.add_all([
            TaskEvent(task_id=task_id, sequence=1, event_type="progress", payload={"progress": 1}),
            TaskEvent(task_id=task_id, sequence=2, event_type="progress", payload={"progress": 2}),
        ])
        db.session.commit()
    def idle(_):
        assert db.engine.pool.checkedout() == 0
    monkeypatch.setattr("app.api.tasks.time.sleep", idle)
    with app.test_request_context(headers={"Last-Event-ID": "1"}):
        g.current_user = SimpleNamespace(id=owner, role_code="normal_user")
        response = task_events(str(task_id))
        try:
            frame = next(response.response)
            assert "id: 2\n" in frame and '"progress": 2' in frame
            assert "id: 1\n" not in frame
            assert db.engine.pool.checkedout() == 0
            # Another request can finish the task while the stream is suspended.
            with db.engine.begin() as connection:
                connection.execute(update(Task).where(Task.id == task_id).values(status=terminal))
            with pytest.raises(StopIteration):
                next(response.response)
            assert db.engine.pool.checkedout() == 0
        finally:
            response.close()


@database_test
def test_chat_releases_quota_transaction_before_waiting_for_model(small_pool_app, monkeypatch):
    def model(**_):
        assert db.engine.pool.checkedout() == 0
        with db.engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
        return {"content": "test reply", "model": "test-model", "usage": {}}
    monkeypatch.setattr("app.api.chat.run_openai_compatible_chat", model)
    monkeypatch.setattr("app.api.chat.record_token_usage", lambda **_: None)
    with small_pool_app.test_request_context(json={"prompt": "test prompt"}):
        g.current_user = SimpleNamespace(id=uuid4(), role_code="normal_user")
        response, status = chat()
        assert status == 200
        assert response.get_json()["data"]["content"] == "test reply"


@database_test
def test_platform_runtime_releases_read_connection_before_http(small_pool_app, monkeypatch):
    class ModelResponse:
        def __enter__(self):
            assert db.engine.pool.checkedout() == 0
            return self
        def __exit__(self, *_):
            pass
        def read(self):
            return b'{"choices":[{"message":{"content":"test reply"}}]}'
    monkeypatch.setattr("app.llm.request.urlopen", lambda *_args, **_kwargs: ModelResponse())
    with small_pool_app.app_context():
        result = run_openai_compatible_chat(messages=[{"role": "user", "content": "test"}])
        assert result["content"] == "test reply"
        assert db.engine.pool.checkedout() == 0


@database_test
def test_platform_settings_failure_does_not_rollback_callers_work(small_pool_app, monkeypatch):
    from sqlalchemy.exc import OperationalError
    with small_pool_app.app_context():
        code = "pending_" + uuid4().hex
        pending = Role(code=code, name="pending caller write")
        db.session.add(pending)
        def unavailable(*_, **__):
            raise OperationalError("test", {}, Exception("simulated DB failure"))
        monkeypatch.setattr(db.engine, "connect", unavailable)
        get_platform_model_runtime()
        assert pending in db.session.new
