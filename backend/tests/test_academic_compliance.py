import json
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from flask import g

from app import create_app
from app.agents.academic_compliance.service import (
    AcademicComplianceService,
    normalize_compliance_payload,
    read_compliance_artifacts,
)
from app.api.tasks import create_task, normalize_compliance_task_options
from app.api.uploads import resolve_manuscript_upload, upload_manuscript
from app.services.catalog_setup import ACADEMIC_COMPLIANCE_AGENT


def test_compliance_options_are_normalized():
    assert normalize_compliance_task_options(
        {
            "compliance_task_type": "journal_submission",
            "compliance_rule_set": " journal-default ",
        }
    ) == {
        "task_type": "journal_submission",
        "target_rule_set": "journal-default",
    }
    assert normalize_compliance_task_options(
        {"compliance_task_type": "unsupported"}
    )["task_type"] == "paper_precheck"


def test_compliance_payload_normalizes_legacy_structured_output_envelope():
    payload = normalize_compliance_payload({
        "structured_output": {
            "summary": {"overall_level": "高", "risk_count": 2},
            "compliance_summary": {"score": 62, "summary": "需要修改"},
            "module_check_results": {"paper_norm": {"score": 80}},
            "risks": [{"risk_id": "RISK-1"}],
        }
    })
    assert payload["compliance_summary"]["compliance_score"] == 62
    assert payload["summary"]["risk_count"] == 2
    assert payload["module_check_results"]["paper_norm"]["score"] == 80
    assert len(payload["risks"]) == 1


def test_manuscript_upload_is_user_isolated_and_validated(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "COMPLIANCE_UPLOAD_DIR": tmp_path,
            "COMPLIANCE_UPLOAD_MAX_BYTES": 1024,
        }
    )
    owner_id = uuid4()
    other_id = uuid4()
    with app.test_request_context(
        method="POST",
        data={"file": (BytesIO("# Paper\nReference [1]".encode()), "paper.md")},
        content_type="multipart/form-data",
    ):
        g.current_user = SimpleNamespace(id=owner_id)
        response, status = upload_manuscript()
        upload_id = response.get_json()["data"]["uploadId"]
        assert status == 201
        assert resolve_manuscript_upload(owner_id, upload_id) is not None
        assert resolve_manuscript_upload(other_id, upload_id) is None

    with app.test_request_context(
        method="POST",
        data={"file": (BytesIO(b"not-a-pdf"), "paper.pdf")},
        content_type="multipart/form-data",
    ):
        g.current_user = SimpleNamespace(id=owner_id)
        response, status = upload_manuscript()
        assert status == 415
        assert response.get_json()["error"]["code"] == "MANUSCRIPT_FILE_INVALID"


def test_compliance_source_is_required_when_agent_is_selected_by_id(monkeypatch):
    app = create_app({"TESTING": True})
    agent = SimpleNamespace(id=uuid4(), code="academic_compliance", status="ACTIVE")
    monkeypatch.setattr("app.api.tasks.resolve_agent", lambda _payload: agent)

    with app.test_request_context(
        method="POST",
        json={"prompt": "检查论文合规性", "agent_id": str(agent.id)},
    ):
        g.current_user = SimpleNamespace(id=uuid4())
        response, status = create_task()

    assert status == 400
    assert response.get_json()["error"]["code"] == "COMPLIANCE_SOURCE_REQUIRED"


def test_compliance_command_uses_external_agent_and_private_model_env(monkeypatch, tmp_path):
    root = tmp_path / "academic-compliance-agent"
    (root / "app" / "graph").mkdir(parents=True)
    (root / "main.py").write_text("", encoding="utf-8")
    app = create_app(
        {
            "TESTING": True,
            "COMPLIANCE_AGENT_ROOT": root,
            "COMPLIANCE_AGENT_TIMEOUT_SECONDS": 321,
            "COMPLIANCE_AGENT_USE_LLM": True,
            "COMPLIANCE_AGENT_MEMORY_ENABLED": False,
        }
    )
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "app.agents.academic_compliance.service.subprocess.run",
        fake_run,
    )
    manuscript = tmp_path / "paper.md"
    manuscript.write_text("# Paper", encoding="utf-8")
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    result = AcademicComplianceService(app).run_core(
        manuscript,
        output_dir,
        uuid4(),
        uuid4(),
        {"task_type": "journal_submission", "target_rule_set": "default"},
        {
            "base_url": "https://personal.example/v1",
            "model_name": "private-model",
            "api_key": "private-secret",
            "timeout_seconds": 180,
        },
    )

    command = captured["command"]
    assert result.returncode == 0
    assert command[0] == sys.executable
    assert command[1] == str(root / "main.py")
    assert captured["cwd"] == str(root)
    assert captured["timeout"] == 321
    assert "private-secret" not in " ".join(command)
    assert captured["env"]["OPENAI_API_KEY"] == "private-secret"
    assert captured["env"]["OPENAI_BASE_URL"] == "https://personal.example/v1"
    assert captured["env"]["OPENAI_MODEL"] == "private-model"
    assert captured["env"]["COMPLIANCE_AGENT_LLM_TIMEOUT"] == "180"
    assert captured["env"]["COMPLIANCE_AGENT_MEMORY_ENABLED"] == "false"
    assert command[command.index("--task-type") + 1] == "journal_submission"


def test_compliance_artifact_contract_and_path_confinement(tmp_path):
    output_dir = tmp_path / "task-output"
    output_dir.mkdir()
    report_path = output_dir / "fixture_report.md"
    result_path = output_dir / "fixture_result.json"
    report_path.write_text("# Compliance report", encoding="utf-8")
    payload = {
        "summary": {"overall_level": "low"},
        "compliance_summary": {"compliance_score": 88},
        "risks": [],
        "module_check_results": {},
    }
    result_path.write_text(json.dumps(payload), encoding="utf-8")

    parsed, resolved_report, resolved_json = read_compliance_artifacts(
        f"Report: {report_path}\nJSON: {result_path}\n",
        output_dir,
    )
    assert parsed == payload
    assert resolved_report == report_path.resolve()
    assert resolved_json == result_path.resolve()

    report_path.unlink()
    result_path.unlink()
    outside_report = tmp_path / "outside_report.md"
    outside_result = tmp_path / "outside_result.json"
    outside_report.write_text("# Outside", encoding="utf-8")
    outside_result.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="未生成预期报告"):
        read_compliance_artifacts(
            f"Report: {outside_report}\nJSON: {outside_result}\n",
            output_dir,
        )


def test_compliance_catalog_points_to_external_runtime():
    assert ACADEMIC_COMPLIANCE_AGENT["code"] == "academic_compliance"
    assert ACADEMIC_COMPLIANCE_AGENT["config_json"]["runtime"] == "academic_compliance_agent"
    assert ACADEMIC_COMPLIANCE_AGENT["config_json"]["route"] == "/agents/academic-compliance"
