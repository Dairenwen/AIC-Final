import json
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from datetime import UTC, datetime

import pytest
from flask import g

from app import create_app
from app.config import Config
from app.agents.academic_translation.service import (
    AcademicTranslationService,
    normalize_translation_options,
    normalize_translation_warning_items,
    parse_translation_stdout,
    resolve_translation_artifacts,
)
from app.api.tasks import (
    create_task,
    normalize_translation_task_options,
    serialize_task,
    serialize_task_output,
)
from app.api.uploads import resolve_translation_upload, upload_translation_document
from app.services.catalog_setup import ACADEMIC_TRANSLATION_AGENT


def test_translation_options_are_normalized_and_bounded():
    options = normalize_translation_options(
        {
            "source_lang": "EN",
            "target_lang": "zh",
            "precision": "submission",
            "parallel": 99,
            "pdf_timeout_seconds": 99999,
            "glossary": {"foundation model": "基础模型"},
            "preserve_pdf_layout": True,
        }
    )
    assert options["source_lang"] == "en"
    assert options["target_lang"] == "zh"
    assert options["precision"] == "submission"
    assert options["parallel"] == 5
    assert options["pdf_timeout_seconds"] == 3600
    assert options["glossary"] == {"foundation model": "基础模型"}
    with pytest.raises(ValueError, match="不能相同"):
        normalize_translation_options({"source_lang": "en", "target_lang": "en"})
    with pytest.raises(ValueError, match="源语言仅支持"):
        normalize_translation_options({"source_lang": "ru", "target_lang": "zh"})


def test_translation_defaults_to_openai_compatible_api():
    assert Config.TRANSLATION_LLM_BACKEND == "api"
    assert Config.TRANSLATION_API_BASE_URL == "https://api.siliconflow.cn/v1"
    assert Config.TRANSLATION_API_MODEL == "deepseek-ai/DeepSeek-V4-Flash"


def test_translation_upload_is_user_isolated_and_validated(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "TRANSLATION_UPLOAD_DIR": tmp_path,
            "TRANSLATION_UPLOAD_MAX_BYTES": 1024,
        }
    )
    owner_id = uuid4()
    other_id = uuid4()
    with app.test_request_context(
        method="POST",
        data={"file": (BytesIO(b"# Abstract\nA foundation model."), "paper.md")},
        content_type="multipart/form-data",
    ):
        g.current_user = SimpleNamespace(id=owner_id)
        response, status = upload_translation_document()
        upload_id = response.get_json()["data"]["uploadId"]
        assert status == 201
        assert resolve_translation_upload(owner_id, upload_id) is not None
        assert resolve_translation_upload(other_id, upload_id) is None

    with app.test_request_context(
        method="POST",
        data={"file": (BytesIO(b"not-a-pdf"), "paper.pdf")},
        content_type="multipart/form-data",
    ):
        g.current_user = SimpleNamespace(id=owner_id)
        response, status = upload_translation_document()
        assert status == 415
        assert response.get_json()["error"]["code"] == "TRANSLATION_FILE_INVALID"


def test_translation_source_is_required(monkeypatch):
    app = create_app({"TESTING": True})
    agent = SimpleNamespace(id=uuid4(), code="academic_translation", status="ACTIVE")
    monkeypatch.setattr("app.api.tasks.resolve_agent", lambda _payload: agent)
    with app.test_request_context(
        method="POST",
        json={"prompt": "翻译论文", "agent_code": "academic_translation"},
    ):
        g.current_user = SimpleNamespace(id=uuid4())
        response, status = create_task()
    assert status == 400
    assert response.get_json()["error"]["code"] == "TRANSLATION_SOURCE_REQUIRED"


def test_translation_task_options_accept_glossary_json():
    options = normalize_translation_task_options(
        {
            "translation_source_lang": "en",
            "translation_target_lang": "zh",
            "translation_glossary": '{"graph neural network":"图神经网络"}',
        }
    )
    assert options["glossary"] == {"graph neural network": "图神经网络"}
    with pytest.raises(ValueError, match="有效 JSON"):
        normalize_translation_task_options({"translation_glossary": "broken"})
    with pytest.raises(ValueError, match="非空文本"):
        normalize_translation_task_options(
            {
                "translation_source_lang": "en",
                "translation_target_lang": "zh",
                "translation_glossary": {"term": 42},
            }
        )


def test_translation_task_rejects_preserve_layout_for_non_pdf_upload(monkeypatch, tmp_path):
    app = create_app({"TESTING": True, "TRANSLATION_UPLOAD_DIR": tmp_path})
    user_id = uuid4()
    upload_id = uuid4()
    user_dir = tmp_path / str(user_id)
    user_dir.mkdir(parents=True)
    (user_dir / f"{upload_id}.md").write_text("# paper", encoding="utf-8")
    agent = SimpleNamespace(id=uuid4(), code="academic_translation", status="ACTIVE")
    monkeypatch.setattr("app.api.tasks.resolve_agent", lambda _payload: agent)
    monkeypatch.setattr("app.api.tasks.resolve_agent_service", lambda _code: SimpleNamespace(start=lambda *_args: None))

    with app.test_request_context(
        method="POST",
        json={
            "prompt": "翻译论文",
            "agent_code": "academic_translation",
            "attachment": "paper.md",
            "attachment_id": str(upload_id),
            "translation_source_lang": "en",
            "translation_target_lang": "zh",
            "translation_preserve_pdf_layout": True,
        },
    ):
        g.current_user = SimpleNamespace(id=user_id)
        response, status = create_task()

    assert status == 400
    assert response.get_json()["error"]["code"] == "TRANSLATION_OPTIONS_INVALID"
    assert "仅适用于 PDF 文件" in response.get_json()["error"]["message"]


def test_translation_command_uses_agent_source_and_isolated_output(monkeypatch, tmp_path):
    root = tmp_path / "academic-translation-agent"
    cli = root / "agent-core" / "src" / "academic_translation" / "cli.py"
    cli.parent.mkdir(parents=True)
    cli.write_text("", encoding="utf-8")
    (root / "agent-core" / "prompts").mkdir(parents=True)
    app = create_app(
        {
            "TESTING": True,
            "TRANSLATION_AGENT_ROOT": root,
            "TRANSLATION_AGENT_TIMEOUT_SECONDS": 321,
            "TRANSLATION_LLM_BACKEND": "api",
            "TRANSLATION_API_BASE_URL": "https://api.example/v1",
            "TRANSLATION_API_MODEL": "provider/translation-model",
            "TRANSLATION_API_KEY": "test-key",
        }
    )
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(
        "app.agents.academic_translation.service.subprocess.run",
        fake_run,
    )
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture")
    output_dir = tmp_path / "task-output"
    output_dir.mkdir()
    options = normalize_translation_options({"bilingual": True, "parallel": 4})

    result = AcademicTranslationService(app).run_core(source, output_dir, options)
    assert result.returncode == 0
    assert captured["command"][:3] == [sys.executable, "-m", "academic_translation.cli"]
    assert captured["cwd"] == str(root / "agent-core")
    assert captured["timeout"] == 321
    assert captured["env"]["PYTHONPATH"].startswith(str(root / "agent-core" / "src"))
    assert captured["env"]["TRANSLATION_LLM_BACKEND"] == "api"
    assert captured["env"]["TRANSLATION_API_BASE_URL"] == "https://api.example/v1"
    assert captured["env"]["TRANSLATION_API_MODEL"] == "provider/translation-model"
    assert captured["command"][captured["command"].index("--output-dir") + 1] == str(output_dir)
    assert "--pdf-bilingual" not in captured["command"]


def test_translation_output_contract_rejects_outside_paths(tmp_path):
    output_dir = tmp_path / "task-output"
    output_dir.mkdir()
    valid = output_dir / "paper-zh.md"
    valid.write_text("译文", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    payload = {
        "task_id": "task",
        "outputs": {"monolingual_markdown": str(valid), "bilingual_markdown": str(outside)},
    }
    parsed = parse_translation_stdout("log before\n" + json.dumps(payload) + "\n")
    artifacts = resolve_translation_artifacts(parsed["outputs"], output_dir)
    assert artifacts == {"monolingual_markdown": str(valid.resolve())}


def test_translation_warning_items_are_structured():
    warning_items = normalize_translation_warning_items(
        [
            "图 2 标签未翻译",
            {"code": "FIGURE_SKIPPED", "message": "检测到受保护图片，已跳过图内文字替换"},
        ]
    )
    assert warning_items == [
        {"code": "TRANSLATION_WARNING_1", "message": "图 2 标签未翻译", "level": "warning"},
        {"code": "FIGURE_SKIPPED", "message": "检测到受保护图片，已跳过图内文字替换", "level": "warning"},
    ]


def test_translation_task_serialization_includes_restore_and_runtime_fields():
    task = SimpleNamespace(
        task_type="ACADEMIC_TRANSLATION",
        status="SUCCEEDED",
        input_json={
            "prompt": "翻译这篇论文",
            "attachment": "paper.pdf",
            "attachment_id": str(uuid4()),
            "translation_options": {
                "source_lang": "en",
                "target_lang": "zh",
                "precision": "submission",
                "glossary": {"foundation model": "基础模型"},
                "parallel": 3,
                "preserve_pdf_layout": True,
                "bilingual": True,
                "translate_figures": True,
                "pdf_layout_mode": "pagewise",
                "pdf_timeout_seconds": 900,
            },
        },
        output_json={
            "translation_warnings": ["图 2 使用位图，未执行图中文字替换"],
            "translation_files": [{"kind": "pdf_monolingual", "label": "保留版式译文 PDF", "file_name": "paper-zh.pdf", "size": 1024}],
        },
    )

    output = serialize_task_output(task)

    assert output["translation_request"]["source_lang"] == "en"
    assert output["translation_request"]["preserve_pdf_layout"] is True
    assert output["translation_restore"]["attachment"] == "paper.pdf"
    assert output["translation_restore"]["bilingual"] is True
    assert output["translation_restore"]["pdf_layout_mode"] == "pagewise"
    assert output["translation_runtime"]["status"] == "SUCCEEDED"
    assert output["translation_runtime"]["outcome"] == "warning"
    assert output["translation_runtime"]["feedback_kind"] == "warning"
    assert output["translation_warning_items"][0]["level"] == "warning"


def test_translation_failed_task_exposes_error_detail_in_contract():
    now = datetime.now(UTC)
    task = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        project_id=None,
        conversation_id=None,
        agent_id=uuid4(),
        model_config_id=None,
        task_type="ACADEMIC_TRANSLATION",
        status="FAILED",
        progress=100,
        current_step="学术翻译 Agent 工作流执行失败",
        input_json={
            "prompt": "翻译论文",
            "attachment": "paper.pdf",
            "translation_options": {"source_lang": "en", "target_lang": "zh"},
        },
        output_json={
            "task_error": {
                "kind": "configuration_error",
                "message": "保留 PDF 原版式需要配置 TRANSLATION_PDF2ZH_COMMAND",
                "retryable": False,
                "next_action": "请联系管理员检查翻译运行时和相关环境变量配置",
            }
        },
        safe_error_message="保留 PDF 原版式需要配置 TRANSLATION_PDF2ZH_COMMAND",
        created_at=now,
        updated_at=now,
    )

    payload = serialize_task(task)

    assert payload["error_detail"]["kind"] == "configuration_error"
    assert payload["output"]["translation_runtime"]["status"] == "FAILED"
    assert payload["output"]["translation_runtime"]["outcome"] == "failed"
    assert payload["output"]["translation_runtime"]["error_kind"] == "configuration_error"
    assert payload["output"]["translation_runtime"]["next_action"].startswith("请联系管理员")


def test_translation_service_emits_warning_feedback_and_result_contract(monkeypatch, tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "AGENT_GENERATED_DIR": tmp_path,
            "TRANSLATION_UPLOAD_DIR": tmp_path / "uploads",
            "TRANSLATION_HEARTBEAT_SECONDS": 1,
            "TRANSLATION_OLLAMA_MODEL": "translategemma:12b",
        }
    )
    source = tmp_path / "uploads" / str(uuid4()) / "paper.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.7\nfixture")
    task = SimpleNamespace(
        id=uuid4(),
        input_json={
            "attachment": "paper.pdf",
            "attachment_id": str(uuid4()),
            "translation_options": {
                "source_lang": "en",
                "target_lang": "zh",
                "bilingual": True,
            },
        },
        output_json={},
        status="QUEUED",
        progress=0,
        current_step="等待执行",
        started_at=None,
        finished_at=None,
        trace_summary={},
    )
    output_dir = tmp_path / "academic_translation" / str(task.id)
    output_dir.mkdir(parents=True)
    markdown = output_dir / "paper-zh.md"
    markdown.write_text("译文", encoding="utf-8")
    events = []

    payload = {
        "task_id": "agent-run-1",
        "source_lang": "en",
        "target_lang": "zh",
        "precision": "reading",
        "warnings": ["图 2 使用位图，未执行图中文字替换"],
        "outputs": {"monolingual_markdown": str(markdown)},
    }

    monkeypatch.setattr("app.agents.academic_translation.service.resolve_translation_upload", lambda *_args: source)
    monkeypatch.setattr("app.agents.academic_translation.service.db.session.get", lambda *_args, **_kwargs: task)
    monkeypatch.setattr("app.agents.academic_translation.service.db.session.commit", lambda: None)
    monkeypatch.setattr(
        AcademicTranslationService,
        "run_core",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
    )
    service = AcademicTranslationService(app)
    monkeypatch.setattr(
        service,
        "emit",
        lambda _task, event_type, progress, message, **extra: events.append(
            {"type": event_type, "progress": progress, "message": message, **extra}
        ),
    )

    with app.app_context():
        service.run(task.id, uuid4())

    warning_event = next(item for item in events if item["type"] == "translation.warning")
    completed_event = next(item for item in events if item["type"] == "task.completed")
    assert warning_event["feedback_kind"] == "warning"
    assert warning_event["feedback_level"] == "warning"
    assert warning_event["warning_count"] == 1
    assert completed_event["outcome"] == "warning"
    assert task.output_json["translation_runtime"]["outcome"] == "warning"
    assert task.output_json["translation_warning_items"][0]["message"].startswith("图 2")


def test_translation_catalog_points_to_external_runtime():
    assert ACADEMIC_TRANSLATION_AGENT["code"] == "academic_translation"
    assert ACADEMIC_TRANSLATION_AGENT["config_json"]["runtime"] == "academic-translation-agent"
    assert ACADEMIC_TRANSLATION_AGENT["config_json"]["route"] == "/agents/academic-translation"
