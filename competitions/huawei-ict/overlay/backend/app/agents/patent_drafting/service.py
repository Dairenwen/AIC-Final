from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import select

from ...api.uploads import resolve_patent_upload
from ...extensions import db
from ...models import ModelConfig, PatentDraftingRun, Task
from ...services.task_model_runtime import NO_MODEL_MESSAGE
from ...services.model_credentials import decrypt_api_key
from ..task_service import BuiltinAgentTaskService, public_error_message


COMPLETED_AGENT_STATES = {
    "completed",
    "completed_with_warnings",
    "demo_completed_with_fixture",
}


class PatentDraftingService(BuiltinAgentTaskService):
    agent_label = "patent-drafting"
    failed_message = "专利撰写 Agent 工作流执行失败"

    def _run_with_context(self, task_id: UUID, user_id: UUID) -> None:
        with self.app.app_context():
            try:
                self.run(task_id, user_id)
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                task = db.session.get(Task, task_id)
                record = self._get_record(task_id, user_id)
                if task:
                    safe_error = public_error_message(exc, self.failed_message)
                    task.status = "FAILED"
                    task.progress = 100
                    task.error_code = type(exc).__name__
                    task.safe_error_message = safe_error
                    task.finished_at = datetime.now(UTC)
                    if record:
                        record.status = "FAILED"
                    self.emit(task, "task.failed", 100, self.failed_message, error=safe_error)
                self.app.logger.exception("Patent drafting task %s failed", task_id)
            finally:
                db.session.remove()

    def run(self, task_id: UUID, user_id: UUID) -> None:
        task = db.session.get(Task, task_id)
        if task is None:
            return
        record = self._record_for(task, user_id)
        task.status = "RUNNING"
        task.started_at = datetime.now(UTC)
        record.status = "RUNNING"
        self.emit(task, "task.started", 5, "已启动专利撰写 Agent")

        case_dir = self._prepare_case(task, user_id, record)
        runtime = self._resolve_model_runtime(task, user_id)
        self.emit(task, "patent.materials_ready", 15, "技术材料已整理并建立输入快照")

        command = [
            sys.executable,
            "-m",
            "patent_agent",
            "run",
            "--config",
            str(self._runtime_root() / "config.yaml"),
            "--case",
            str(case_dir),
            "--workflow-mode",
            record.workflow_mode,
        ]
        if bool(self.app.config["PATENT_DRAFTING_FAKE_MODE"]):
            command.append("--fake")
        if bool(self.app.config["PATENT_DRAFTING_ALLOW_FIXTURE_FALLBACK"]):
            command.append("--allow-cnipa-fixture-fallback")

        self.emit(task, "patent.candidates_started", 25, "正在分析可保护技术点")
        result = self._run_command(command, task, runtime)
        payload = _parse_cli_payload(result.stdout)
        if result.returncode != 10:
            raise RuntimeError(_command_error(result, payload, "候选专利点生成失败"))

        run_id = str(payload.get("run_id") or "").strip()
        if not run_id:
            raise RuntimeError("专利撰写 Agent 未返回运行编号")
        record.agent_run_id = run_id
        candidates = self._load_candidates(task, run_id)
        record.candidates = candidates
        record.status = "WAITING_INPUT"
        record.waiting_at = datetime.now(UTC)
        record.run_summary = _public_run_summary(payload)
        task.status = "WAITING_INPUT"
        self.merge_output(
            task,
            patent_run_id=run_id,
            patent_status="waiting_for_patent_point_selection",
            patent_candidates=candidates,
            patent_summary=record.run_summary,
        )
        self.emit(
            task,
            "patent.selection_required",
            35,
            "候选专利点已生成，请选择一项继续撰写",
            candidates=candidates,
        )

    def resume(self, task_id: UUID, user_id: UUID, selected_id: str, notes: str) -> None:
        thread = threading.Thread(
            target=self._resume_with_context,
            args=(task_id, user_id, selected_id, notes),
            name=f"{self.agent_label}-resume-{task_id}",
            daemon=True,
        )
        thread.start()

    def _resume_with_context(
        self, task_id: UUID, user_id: UUID, selected_id: str, notes: str
    ) -> None:
        with self.app.app_context():
            try:
                self._resume(task_id, user_id, selected_id, notes)
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                task = db.session.get(Task, task_id)
                record = self._get_record(task_id, user_id)
                if task:
                    safe_error = public_error_message(exc, self.failed_message)
                    task.status = "FAILED"
                    task.progress = 100
                    task.error_code = type(exc).__name__
                    task.safe_error_message = safe_error
                    task.finished_at = datetime.now(UTC)
                    if record:
                        record.status = "FAILED"
                    self.emit(task, "task.failed", 100, self.failed_message, error=safe_error)
                self.app.logger.exception("Patent drafting task %s failed during resume", task_id)
            finally:
                db.session.remove()

    def _resume(
        self, task_id: UUID, user_id: UUID, selected_id: str, notes: str
    ) -> None:
        task = db.session.get(Task, task_id)
        record = self._get_record(task_id, user_id)
        if task is None or record is None or not record.agent_run_id:
            raise RuntimeError("专利运行记录不存在")
        valid_ids = {str(item.get("id")) for item in record.candidates if isinstance(item, dict)}
        if selected_id not in valid_ids:
            raise RuntimeError("所选专利点不在当前候选列表中")

        record.selected_candidate_id = selected_id
        record.selection_notes = notes[:2000] or None
        record.status = "RUNNING"
        task.status = "RUNNING"
        self.merge_output(task, selected_patent_point_id=selected_id, patent_status="running")
        self.emit(task, "patent.selection_accepted", 42, "已确认专利点，正在规划检索策略")

        task_dir = self._task_dir(task)
        response_path = task_dir / "selection.json"
        response_path.write_text(
            json.dumps(
                {"selected_ids": [selected_id], "notes": notes[:2000]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        command = [
            sys.executable,
            "-m",
            "patent_agent",
            "resume",
            "--config",
            str(self._runtime_root() / "config.yaml"),
            "--run-id",
            record.agent_run_id,
            "--response",
            str(response_path),
        ]
        if bool(self.app.config["PATENT_DRAFTING_ALLOW_FIXTURE_FALLBACK"]):
            command.append("--allow-cnipa-fixture-fallback")
        runtime = self._resolve_model_runtime(task, user_id)
        self.emit(task, "patent.drafting_started", 50, "正在执行专利检索、差异分析与草案撰写")
        result = self._run_command(command, task, runtime)
        payload = _parse_cli_payload(result.stdout)
        if result.returncode != 0:
            raise RuntimeError(_command_error(result, payload, "专利草案生成失败"))
        if str(payload.get("status") or "") not in COMPLETED_AGENT_STATES:
            raise RuntimeError("专利撰写 Agent 未进入完成状态")

        self.emit(task, "patent.artifacts_ready", 92, "交底书、权利要求与校验产物已生成")
        output = self._collect_output(task, record, payload)
        record.status = "SUCCEEDED"
        record.run_summary = output["patent_summary"]
        record.artifacts = output["artifacts"]
        record.completed_at = datetime.now(UTC)
        task.status = "SUCCEEDED"
        task.finished_at = datetime.now(UTC)
        task.trace_summary = {
            "agent": "patent_drafting",
            "agent_run_id": record.agent_run_id,
            "selected_candidate_id": selected_id,
            "provider_mode": payload.get("provider_mode"),
            "search_status": payload.get("search_status"),
            "warning_count": len(payload.get("warnings") or []),
        }
        self.merge_output(task, **output)
        self.emit(task, "task.completed", 100, "专利草案已生成，结果仍需专利专业人员复核")

    def _record_for(self, task: Task, user_id: UUID) -> PatentDraftingRun:
        record = self._get_record(task.id, user_id)
        if record is not None:
            return record
        options = (task.input_json or {}).get("patent_options") or {}
        record = PatentDraftingRun(
            task_id=task.id,
            user_id=user_id,
            status="QUEUED",
            workflow_mode=str(options.get("workflow_mode") or "flow_first"),
            source_file_name=str((task.input_json or {}).get("attachment") or "") or None,
            candidates=[],
            run_summary={},
            artifacts={},
        )
        db.session.add(record)
        db.session.flush()
        return record

    @staticmethod
    def _get_record(task_id: UUID, user_id: UUID) -> PatentDraftingRun | None:
        return db.session.scalar(
            select(PatentDraftingRun).where(
                PatentDraftingRun.task_id == task_id,
                PatentDraftingRun.user_id == user_id,
            )
        )

    def _prepare_case(
        self, task: Task, user_id: UUID, record: PatentDraftingRun
    ) -> Path:
        task_dir = self._task_dir(task)
        case_dir = task_dir / "case"
        materials_dir = case_dir / "materials"
        materials_dir.mkdir(parents=True, exist_ok=True)
        input_json = task.input_json or {}
        prompt = str(input_json.get("prompt") or "").strip()
        options = input_json.get("patent_options") or {}
        title = str(options.get("title") or prompt[:120] or "待撰写专利技术方案").strip()
        (materials_dir / "technical_overview.md").write_text(
            f"# 技术材料说明\n\n{prompt}\n",
            encoding="utf-8",
        )
        upload_id = input_json.get("attachment_id")
        if upload_id:
            source = resolve_patent_upload(user_id, upload_id)
            if source is None:
                raise RuntimeError("上传的技术材料不存在或不属于当前用户")
            destination = materials_dir / f"source_material{source.suffix.lower()}"
            shutil.copy2(source, destination)
        case_payload = {
            "title": title,
            "language": "zh-CN",
            "contact": {"name": "待填写", "phone": "待填写", "email": "待填写"},
        }
        (case_dir / "case.yaml").write_text(
            yaml.safe_dump(case_payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        record.source_file_name = str(input_json.get("attachment") or "") or None
        db.session.commit()
        return case_dir

    def _run_command(
        self, command: list[str], task: Task, runtime: dict[str, object]
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PATENT_AGENT_RUNS_DIR": str(self._task_dir(task) / "runs"),
                "PATENT_AGENT_OUTPUTS_DIR": str(self._task_dir(task) / "outputs"),
                "QWEN_BASE_URL": str(runtime["base_url"]),
                "QWEN_MODEL_NAME": str(runtime["model_name"]),
                "QWEN_API_KEY": str(runtime["api_key"]),
                "QWEN_TIMEOUT": str(runtime["timeout_seconds"]),
                "QWEN_MAX_TOKENS": str(runtime["max_output_tokens"]),
            }
        )
        return subprocess.run(
            command,
            cwd=str(self._runtime_root()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=int(self.app.config["PATENT_DRAFTING_TIMEOUT_SECONDS"]),
        )

    def _resolve_model_runtime(self, task: Task, user_id: UUID) -> dict[str, object]:
        if task.model_config_id is not None:
            item = db.session.get(ModelConfig, task.model_config_id)
            if (
                item is None
                or item.owner_user_id != user_id
                or item.config_scope != "USER"
                or item.status != "ACTIVE"
                or item.deleted_at is not None
            ):
                raise RuntimeError("所选个人模型不存在、未验证或已停用")
            if not item.encrypted_api_key or not item.key_nonce or not item.key_version:
                raise RuntimeError("所选个人模型缺少有效的 API Key")
            settings = item.settings or {}
            return {
                "base_url": item.base_url,
                "model_name": item.model_name,
                "api_key": decrypt_api_key(
                    item.encrypted_api_key, item.key_nonce, item.key_version
                ),
                "timeout_seconds": max(10, min(float(settings.get("timeout_seconds", 120)), 600)),
                "max_output_tokens": max(1024, min(int(settings.get("max_output_tokens", 8192)), 32768)),
            }
        if not self.app.config.get("PLATFORM_MODEL_ENABLED", False):
            raise RuntimeError(NO_MODEL_MESSAGE)
        return {
            "base_url": str(self.app.config["QWEN_DPO_BASE_URL"]),
            "model_name": str(self.app.config["QWEN_DPO_MODEL"]),
            "api_key": str(self.app.config["QWEN_DPO_API_KEY"]),
            "timeout_seconds": float(self.app.config["QWEN_DPO_TIMEOUT_SECONDS"]),
            "max_output_tokens": 8192,
        }

    def _load_candidates(self, task: Task, run_id: str) -> list[dict[str, Any]]:
        path = self._task_dir(task) / "runs" / run_id / "patent_points.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        candidates = payload.get("patent_points") or []
        return [item for item in candidates if isinstance(item, dict)]

    def _collect_output(
        self, task: Task, record: PatentDraftingRun, payload: dict[str, Any]
    ) -> dict[str, Any]:
        run_dir = self._task_dir(task) / "runs" / str(record.agent_run_id)
        artifact_dir = run_dir / "artifacts"

        def read_json(name: str, fallback: Any) -> Any:
            path = artifact_dir / name
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else fallback

        def read_text(name: str) -> str:
            path = artifact_dir / name
            return path.read_text(encoding="utf-8") if path.is_file() else ""

        artifact_names = {
            "patent-disclosure-markdown": "disclosure.md",
            "patent-disclosure-docx": "disclosure.docx",
            "patent-claims-markdown": "claims.md",
            "patent-claims-json": "claims.json",
            "patent-manifest": "manifest.json",
            "patent-disclosure-evidence": "disclosure_evidence_review.md",
            "patent-claim-evidence": "claim_evidence_review.md",
        }
        artifacts = {
            kind: str((artifact_dir / name).resolve())
            for kind, name in artifact_names.items()
            if (artifact_dir / name).is_file()
        }
        return {
            "patent_status": payload.get("status"),
            "patent_summary": _public_run_summary(payload),
            "patent_candidates": record.candidates,
            "selected_patent_point_id": record.selected_candidate_id,
            "disclosure_sections": read_json("disclosure_sections.json", {}),
            "disclosure_markdown": read_text("disclosure.md"),
            "claim_plan": read_json("claim_plan.json", {}),
            "claims": read_json("claims.json", {}),
            "claims_markdown": read_text("claims.md"),
            "claim_validation": read_json("claim_validation.json", {}),
            "release_readiness": payload.get("release_readiness") or {},
            "patent_warnings": payload.get("warnings") or [],
            "artifacts": artifacts,
        }

    def _task_dir(self, task: Task) -> Path:
        root = Path(self.app.config["PATENT_DRAFTING_DATA_DIR"]).resolve()
        path = (root / str(task.user_id) / str(task.id)).resolve()
        if not path.is_relative_to(root):
            raise RuntimeError("专利任务目录越界")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _runtime_root(self) -> Path:
        root = Path(self.app.config["PATENT_DRAFTING_RUNTIME_ROOT"]).resolve()
        if not (root / "patent_agent" / "__main__.py").is_file():
            raise RuntimeError(f"专利撰写 Agent 运行时不完整: {root}")
        return root


def _parse_cli_payload(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return payload if isinstance(payload, dict) else {}


def _command_error(
    result: subprocess.CompletedProcess[str], payload: dict[str, Any], fallback: str
) -> str:
    message = str(payload.get("message") or "").strip()
    if not message and isinstance(payload.get("error"), dict):
        message = str(payload["error"].get("message") or "").strip()
    if not message:
        message = (result.stderr or result.stdout or fallback).strip()[-1500:]
    return message or fallback


def _public_run_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload.get(key)
        for key in (
            "run_id",
            "status",
            "current_stage",
            "pending_action",
            "provider_mode",
            "workflow_mode",
            "search_mode",
            "search_status",
            "quality_gate",
            "claim_validation",
            "release_readiness",
            "warnings",
        )
        if key in payload
    }
