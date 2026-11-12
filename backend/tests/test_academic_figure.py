from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from flask import g

from app import create_app
from app.agents.academic_figure.service import (
    AcademicFigureService,
    _command_error,
    _parse_cli_payload,
    _safe_figure_error,
)
from app.api.tasks import (
    normalize_figure_files,
    normalize_figure_options,
    serialize_task_output,
    validate_figure_inputs,
)
from app.api.tasks import task_artifact
from app.api.uploads import resolve_figure_upload, upload_figure_input
from app.services.catalog_setup import ACADEMIC_FIGURE_AGENT


def test_figure_upload_is_validated_and_user_isolated(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "FIGURE_UPLOAD_DIR": tmp_path,
            "FIGURE_UPLOAD_MAX_BYTES": 1024,
        }
    )
    owner_id = uuid4()
    other_id = uuid4()
    with app.test_request_context(
        method="POST",
        data={"kind": "data", "file": (BytesIO(b"epoch,score\n1,0.8\n"), "metrics.csv")},
        content_type="multipart/form-data",
    ):
        g.current_user = SimpleNamespace(id=owner_id)
        response, status = upload_figure_input()
        payload = response.get_json()["data"]
        assert status == 201
        assert resolve_figure_upload(owner_id, payload["uploadId"], "data") is not None
        assert resolve_figure_upload(other_id, payload["uploadId"], "data") is None

    with app.test_request_context(
        method="POST",
        data={"kind": "sketch", "file": (BytesIO(b"not-an-image"), "draft.png")},
        content_type="multipart/form-data",
    ):
        g.current_user = SimpleNamespace(id=owner_id)
        response, status = upload_figure_input()
        assert status == 415
        assert response.get_json()["error"]["code"] == "FIGURE_FILE_INVALID"


def test_figure_options_and_input_contract_are_normalized():
    options = normalize_figure_options(
        {
            "figure_type": "SCATTER",
            "figure_planning_mode": "offline",
            "figure_export_formats": ["PNG", "svg", "PNG"],
            "figure_code_formats": ["r"],
            "figure_languages": ["ZH"],
        }
    )
    assert options == {
        "figure_type": "scatter",
        "planning_mode": "offline",
        "export_formats": ["png", "svg"],
        "code_formats": ["python", "r"],
        "languages": ["zh"],
    }
    files = normalize_figure_files(
        {
            "figure_files": [
                {"kind": "data", "uploadId": str(uuid4()), "fileName": "../metrics.csv"}
            ]
        }
    )
    assert files[0]["file_name"] == "metrics.csv"
    validate_figure_inputs(options, files)
    with pytest.raises(ValueError, match="统计图"):
        validate_figure_inputs(options, [])
    with pytest.raises(ValueError, match="图表类型"):
        normalize_figure_options({"figure_type": "arbitrary_python"})


def test_figure_command_passes_api_key_only_through_environment(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    package = runtime_root / "src" / "academic_figure_agent"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (runtime_root / "main.py").write_text("", encoding="utf-8")
    app = create_app(
        {
            "TESTING": True,
            "ACADEMIC_FIGURE_RUNTIME_ROOT": runtime_root,
            "ACADEMIC_FIGURE_DATA_DIR": tmp_path / "generated",
            "ACADEMIC_FIGURE_TIMEOUT_SECONDS": 25,
        }
    )
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("app.agents.academic_figure.service.subprocess.run", fake_run)
    task = SimpleNamespace(id=uuid4(), user_id=uuid4())
    request_path = tmp_path / "request.json"
    request_path.write_text("{}", encoding="utf-8")
    with app.app_context():
        AcademicFigureService(app)._run_command(
            task,
            request_path,
            {
                "base_url": "https://model.example/v1",
                "model_name": "qwen-test",
                "api_key": "secret-not-in-command",
                "timeout_seconds": 120,
            },
        )

    assert "secret-not-in-command" not in " ".join(captured["command"])
    assert captured["env"]["DASHSCOPE_API_KEY"] == "secret-not-in-command"
    assert captured["env"]["MPLCONFIGDIR"].endswith("matplotlib-cache")
    assert captured["env"]["BAILIAN_MAX_RETRIES"] == "4"
    assert captured["env"]["BAILIAN_ALLOW_OFFLINE_FALLBACK"] == "true"
    assert captured["timeout"] == 25


def test_figure_output_collection_uses_task_bundle_only(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "ACADEMIC_FIGURE_DATA_DIR": tmp_path / "generated",
        }
    )
    task_dir = tmp_path / "generated" / "user" / "task"
    bundle = task_dir / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "figure_zh.png").write_bytes(b"png")
    (bundle / "quality_report.json").write_text("{}", encoding="utf-8")
    with app.app_context():
        output = AcademicFigureService(app)._collect_output(
            task_dir,
            {
                "spec": {"figure_type": "line"},
                "dataset": {"row_count": 2},
                "captions": {"zh": "图注"},
                "quality_report": {"passed": True},
            },
        )
    assert set(output["artifacts"]) == {"figure-zh-png", "figure-quality"}
    assert Path(output["artifacts"]["figure-zh-png"]).is_relative_to(bundle.resolve())


def test_figure_cli_parser_and_catalog_contract():
    assert _parse_cli_payload('runtime log\n{"spec":{"figure_type":"bar"}}') == {
        "spec": {"figure_type": "bar"}
    }
    assert ACADEMIC_FIGURE_AGENT["code"] == "academic_figure"
    assert ACADEMIC_FIGURE_AGENT["config_json"]["route"] == "/agents/academic-figure"
    assert ACADEMIC_FIGURE_AGENT["config_json"]["runtime"] == "academic-figure-agent"


def test_figure_task_response_does_not_expose_server_paths(tmp_path):
    secret_root = tmp_path / "private" / "task"
    task = SimpleNamespace(
        task_type="ACADEMIC_FIGURE",
        output_json={
            "artifacts": {"figure-zh-png": str(secret_root / "bundle" / "figure_zh.png")},
            "figure_artifacts": {"output_dir": str(secret_root / "bundle")},
            "dataset_summary": {
                "normalized_path": str(secret_root / "bundle" / "source_data.csv"),
                "source_files": [str(secret_root / "inputs" / "data.csv")],
            },
            "figure_request": {
                "output_dir": str(secret_root / "bundle"),
                "data_files": [str(secret_root / "inputs" / "data.csv")],
            },
            "figure_quality": {
                "passed": True,
                "generated_files": [str(secret_root / "bundle" / "figure_zh.png")],
            },
        },
    )
    output = serialize_task_output(task)
    assert output["artifacts"] == {"figure-zh-png": "figure-zh-png"}
    assert "figure_artifacts" not in output
    assert output["dataset_summary"]["source_files"] == ["data.csv"]
    assert output["figure_request"]["data_files"] == ["data.csv"]
    assert output["figure_quality"]["generated_files"] == ["figure_zh.png"]
    assert str(secret_root) not in str(output)


def test_figure_runtime_errors_are_safe_for_task_responses():
    result = SimpleNamespace(
        stderr=(
            'Traceback (most recent call last):\n  File "E:/private/service.py", line 1\n'
            "openai.InternalServerError: Error code: 502"
        ),
        stdout="",
    )
    message = _command_error(result, {})
    assert message == "模型服务暂时不可用，在线规划未完成。请重试或选择离线规则规划。"
    assert "E:/private" not in message
    assert _safe_figure_error(RuntimeError(result.stderr)) == message


def test_figure_code_artifact_uses_portable_bundle_path(monkeypatch, tmp_path):
    app = create_app({"TESTING": True, "ACADEMIC_FIGURE_DATA_DIR": tmp_path / "figures"})
    task_id = uuid4()
    user_id = uuid4()
    bundle = tmp_path / "figures" / str(user_id) / str(task_id) / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "figure.py").write_text("print('ok')", encoding="utf-8")
    task = SimpleNamespace(
        id=task_id,
        user_id=user_id,
        output_json={"artifacts": {"figure-code-python": "E:/old-deployment/figure.py"}},
    )
    monkeypatch.setattr("app.api.tasks.find_task", lambda _task_id: task)
    with app.test_request_context():
        g.current_user = SimpleNamespace(id=user_id, role_code="normal_user")
        response = task_artifact(str(task_id), "figure-code-python")
    assert response.status_code == 200
