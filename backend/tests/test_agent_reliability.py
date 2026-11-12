from __future__ import annotations

import json
import subprocess
import sys
import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app import create_app
from app.api import tasks as tasks_api
from app.agents.academic_translation.service import run_with_heartbeat
from app.agents.manuscript_assistance.service import (
    ManuscriptAssistanceService,
    build_deterministic_manuscript,
    build_manuscript_plan,
)
from app.agents.task_service import public_error_message
from app.extensions import db
from app.services.agent_readiness import agent_readiness, clear_readiness_cache


def test_public_errors_do_not_expose_stack_or_absolute_paths():
    error = RuntimeError(
        "Traceback (most recent call last):\n"
        "  File \"E:\\private\\agent.py\", line 10\n"
        "RuntimeError: failed"
    )
    message = public_error_message(error, "智能体执行失败")
    assert message == "智能体执行失败"
    assert "E:\\private" not in message
    assert "Traceback" not in message


def test_manuscript_fallback_is_explicit_and_structured(monkeypatch, tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "AGENT_GENERATED_DIR": tmp_path,
            "MANUSCRIPT_ALLOW_DETERMINISTIC_FALLBACK": True,
        }
    )
    task = SimpleNamespace(
        id=uuid4(),
        input_json={"prompt": "面向科研智能体可靠性评测的论文"},
        model_config_id=None,
        output_json={},
        status="QUEUED",
        progress=0,
        current_step="等待执行",
        started_at=None,
        finished_at=None,
        trace_summary={},
    )
    service = ManuscriptAssistanceService(app)
    monkeypatch.setattr(service, "run_core", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("model unavailable")))
    monkeypatch.setattr(service, "emit", lambda *_args, **_kwargs: None)

    with app.app_context():
        monkeypatch.setattr(db.session, "get", lambda *_args, **_kwargs: task)
        monkeypatch.setattr(db.session, "commit", lambda: None)
        service.run(task.id, uuid4())

    assert task.status == "SUCCEEDED"
    assert task.output_json["manuscript_execution_mode"] == "deterministic_fallback"
    assert task.output_json["manuscript_warnings"]
    assert "## 方法设计" in task.output_json["manuscript_markdown"]
    assert len(task.output_json["sections"]) == 6


def test_deterministic_manuscript_contains_no_unverified_results():
    prompt = "动态 RAG 安全评估"
    markdown = build_deterministic_manuscript(prompt, build_manuscript_plan(prompt))
    assert "具体数据、结论与引用需由作者补充并核验" in markdown
    assert "## 实验方案" in markdown


def test_manuscript_independent_body_sections_are_concurrent():
    core_dir = Path(__file__).resolve().parents[1] / "app" / "agents" / "Manuscript assistance" / "agent-core"
    sys.path.insert(0, str(core_dir))
    try:
        from src.orchestrator.orchestrator_agent import OrchestratorAgent

        started: list[str] = []
        release = asyncio.Event()

        class FakeAgent:
            def __init__(self, name: str):
                self.name = name

            async def run(self, _state):
                started.append(self.name)
                await release.wait()
                return {"title": self.name, "content": self.name, "word_count": 1, "quality_score": 0.0, "iteration_count": 1}

        async def exercise() -> dict:
            orchestrator = OrchestratorAgent.__new__(OrchestratorAgent)
            orchestrator.agents = {name: FakeAgent(name) for name in ("introduction", "related_work", "method", "experiment")}
            state = {"sections": {}}
            pending = asyncio.create_task(orchestrator._write_independent_sections(state))
            for _ in range(20):
                if len(started) == 4:
                    break
                await asyncio.sleep(0)
            assert started == ["introduction", "related_work", "method", "experiment"]
            release.set()
            await pending
            return state

        state = asyncio.run(exercise())
    finally:
        sys.path.remove(str(core_dir))

    assert list(state["sections"]) == ["introduction", "related_work", "method", "experiment"]


def test_translation_process_reports_heartbeats():
    heartbeats: list[float] = []
    result = run_with_heartbeat(
        [sys.executable, "-c", "import time; time.sleep(0.15); print('done')"],
        timeout_seconds=5,
        heartbeat_seconds=0.04,
        progress_callback=heartbeats.append,
        cwd=str(Path.cwd()),
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "done"
    assert len(heartbeats) >= 2


def test_readiness_marks_model_fallback_and_missing_runtime(monkeypatch, tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "INNOVATION_AGENT_ROOT": tmp_path / "missing-innovation-runtime",
            "AGENT_READINESS_CACHE_SECONDS": 5,
        }
    )
    monkeypatch.setattr("app.services.agent_readiness.endpoint_reachable", lambda *_args: False)
    clear_readiness_cache()

    manuscript = agent_readiness(app, "manuscript_assistance")
    innovation = agent_readiness(app, "innovation_point_generation")
    assert manuscript["readiness"] == "DEGRADED"
    assert manuscript["status"] == "降级可用"
    assert innovation["readiness"] == "UNAVAILABLE"


def test_task_creation_rejects_unavailable_agent_before_persisting(monkeypatch):
    app = create_app({"TESTING": True})
    user = SimpleNamespace(id=uuid4())
    agent = SimpleNamespace(id=uuid4(), code="paper_reading")
    service = SimpleNamespace(start=lambda *_args: None)

    monkeypatch.setattr(tasks_api, "resolve_agent", lambda _payload: agent)
    monkeypatch.setattr(tasks_api, "resolve_agent_service", lambda _code: service)
    monkeypatch.setattr(tasks_api, "resolve_user", lambda: user)
    monkeypatch.setattr(
        tasks_api,
        "agent_readiness",
        lambda *_args, **_kwargs: {
            "readiness": "UNAVAILABLE",
            "status": "依赖异常",
            "readiness_detail": "平台模型服务不可达",
        },
    )
    monkeypatch.setattr(db.session, "add", lambda _task: (_ for _ in ()).throw(AssertionError("must not persist")))

    with app.test_request_context(
        "/api/v1/tasks",
        method="POST",
        json={
            "prompt": "精读论文",
            "agent_code": "paper_reading",
            "link": "https://arxiv.org/abs/1706.03762",
        },
    ):
        response, status = tasks_api.create_task()

    assert status == 409
    assert response.get_json()["error"]["code"] == "AGENT_DEPENDENCY_UNAVAILABLE"


def test_health_is_degraded_when_an_agent_is_not_ready(monkeypatch):
    app = create_app({"TESTING": True})
    monkeypatch.setattr(db.session, "execute", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.api.health.agent_readiness",
        lambda _app, code: {
            "readiness": "UNAVAILABLE" if code == "paper_reading" else "READY",
            "status": "依赖异常" if code == "paper_reading" else "可用",
            "readiness_detail": "test",
        },
    )

    response = app.test_client().get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.get_json()["data"]["status"] == "degraded"


def test_paper_reading_json_parser_accepts_reasoning_and_fences():
    core_src = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "agents"
        / "paper_reading"
        / "runtime"
        / "agent-core"
        / "src"
    )
    sys.path.insert(0, str(core_src))
    try:
        from llm.openai_compatible import _json_content

        payload = {"claims": [{"claim_id": "c1"}], "warnings": []}
        wrapped = f"<think>internal reasoning</think>\n```json\n{json.dumps(payload)}\n```\n完成"
        assert _json_content(wrapped) == payload
    finally:
        sys.path.remove(str(core_src))
