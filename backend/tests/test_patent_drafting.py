from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from flask import g

from app import create_app
from app.agents.patent_drafting.service import PatentDraftingService, _parse_cli_payload
from app.api.tasks import normalize_patent_options, submit_patent_selection
from app.api.uploads import upload_patent_material
from app.extensions import db


def test_patent_upload_is_stored_inside_user_directory(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "PATENT_UPLOAD_DIR": tmp_path,
            "PATENT_UPLOAD_MAX_BYTES": 1024,
        }
    )
    user_id = uuid4()
    with app.test_request_context(
        method="POST",
        data={"file": (BytesIO("技术方案材料".encode()), "material.md")},
        content_type="multipart/form-data",
    ):
        g.current_user = SimpleNamespace(id=user_id)
        response, status = upload_patent_material()

    payload = response.get_json()["data"]
    assert status == 201
    assert (tmp_path / str(user_id) / f"{payload['uploadId']}.md").is_file()


def test_patent_options_reject_unknown_workflow_mode():
    try:
        normalize_patent_options({"patent_workflow_mode": "unsafe"}, "技术方案")
    except ValueError as exc:
        assert "工作流模式" in str(exc)
    else:
        raise AssertionError("invalid workflow mode was accepted")


def test_cli_payload_parser_accepts_preceding_log_lines():
    assert _parse_cli_payload('runtime log\n{"run_id":"run-1","status":"completed"}') == {
        "run_id": "run-1",
        "status": "completed",
    }


def test_patent_command_passes_api_key_only_through_environment(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    (runtime_root / "patent_agent").mkdir(parents=True)
    (runtime_root / "patent_agent" / "__main__.py").write_text("", encoding="utf-8")
    app = create_app(
        {
            "TESTING": True,
            "PATENT_DRAFTING_RUNTIME_ROOT": runtime_root,
            "PATENT_DRAFTING_DATA_DIR": tmp_path / "data",
            "PATENT_DRAFTING_TIMEOUT_SECONDS": 20,
        }
    )
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=10, stdout='{"run_id":"run-1"}', stderr="")

    monkeypatch.setattr("app.agents.patent_drafting.service.subprocess.run", fake_run)
    task = SimpleNamespace(id=uuid4(), user_id=uuid4())
    with app.app_context():
        PatentDraftingService(app)._run_command(
            ["python", "-m", "patent_agent", "run"],
            task,
            {
                "base_url": "https://model.example/v1",
                "model_name": "qwen-test",
                "api_key": "secret-not-in-command",
                "timeout_seconds": 120,
                "max_output_tokens": 8192,
            },
        )

    assert "secret-not-in-command" not in " ".join(captured["command"])
    assert captured["env"]["QWEN_API_KEY"] == "secret-not-in-command"
    assert captured["env"]["PATENT_AGENT_RUNS_DIR"].endswith("runs")


def test_patent_selection_resumes_waiting_task(monkeypatch):
    app = create_app({"TESTING": True})
    user_id = uuid4()
    task = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        agent_id=uuid4(),
        model_config_id=None,
        task_type="PATENT_DRAFTING",
        input_json={"prompt": "技术方案", "agent_code": "patent_drafting"},
        output_json={"patent_candidates": [{"id": "PP-001"}]},
        status="WAITING_INPUT",
        progress=35,
        current_step="等待选择",
        safe_error_message=None,
        created_at=None,
        updated_at=None,
    )
    record = SimpleNamespace(candidates=[{"id": "PP-001"}], status="WAITING_INPUT")
    resumed = {}
    service = SimpleNamespace(
        resume=lambda task_id, resume_user_id, selected_id, notes: resumed.update(
            task_id=task_id,
            user_id=resume_user_id,
            selected_id=selected_id,
            notes=notes,
        )
    )
    monkeypatch.setattr("app.api.tasks.find_task", lambda _task_id: task)
    monkeypatch.setattr("app.api.tasks.resolve_agent_service", lambda _code: service)
    monkeypatch.setattr(db.session, "scalar", lambda _query: record)
    monkeypatch.setattr(db.session, "commit", lambda: None)

    with app.test_request_context(
        method="POST",
        json={"selected_id": "PP-001", "notes": "选择主保护路线"},
    ):
        g.current_user = SimpleNamespace(id=user_id)
        response, status = submit_patent_selection(str(task.id))

    assert status == 202
    assert response.get_json()["data"]["status"] == "QUEUED"
    assert resumed["selected_id"] == "PP-001"
    assert record.status == "QUEUED"
