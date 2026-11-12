from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from ...api.uploads import resolve_figure_upload
from ...extensions import db
from ...models import AcademicFigureRun, ModelConfig, Task
from ...services.model_credentials import decrypt_api_key
from ...services.system_model_runtime import get_owned_model_runtime, get_system_model_runtime
from ..task_service import BuiltinAgentTaskService


class AcademicFigureService(BuiltinAgentTaskService):
    agent_label = "academic-figure"
    failed_message = "绘图创作 Agent 工作流执行失败"

    def _run_with_context(self, task_id: UUID, user_id: UUID) -> None:
        with self.app.app_context():
            try:
                self.run(task_id, user_id)
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                task = db.session.get(Task, task_id)
                record = self._get_record(task_id, user_id)
                if task:
                    safe_error = _safe_figure_error(exc)
                    task.status = "FAILED"
                    task.progress = 100
                    task.error_code = type(exc).__name__
                    task.safe_error_message = safe_error
                    task.finished_at = datetime.now(UTC)
                    if record:
                        record.status = "FAILED"
                    self.emit(task, "task.failed", 100, self.failed_message, error=safe_error)
                self.app.logger.exception("Academic figure task %s failed", task_id)
            finally:
                db.session.remove()

    def run(self, task_id: UUID, user_id: UUID) -> None:
        task = db.session.get(Task, task_id)
        if task is None:
            return
        input_json = task.input_json or {}
        options = input_json.get("figure_options") or {}
        record = self._record_for(task, user_id, options)
        task.status = "RUNNING"
        task.started_at = datetime.now(UTC)
        record.status = "RUNNING"
        self.emit(task, "task.started", 5, "已启动绘图创作 Agent")

        task_dir = self._task_dir(task)
        inputs = self._snapshot_inputs(task, user_id, task_dir)
        record.input_files = inputs["metadata"]
        self.emit(
            task,
            "figure.sources_ready",
            18,
            "绘图数据、上下文与草图已完成校验",
            file_count=len(inputs["metadata"]),
        )

        request_payload = {
            "prompt": str(input_json.get("prompt") or "").strip(),
            "data_files": inputs["data"],
            "context_files": inputs["context"],
            "sketch_files": inputs["sketch"],
            "output_dir": str((task_dir / "bundle").resolve()),
            "figure_type": str(options.get("figure_type") or "auto"),
            "export_formats": list(options.get("export_formats") or ["pdf", "svg", "png"]),
            "code_formats": list(options.get("code_formats") or ["python", "r", "latex", "mermaid"]),
            "languages": list(options.get("languages") or ["zh", "en"]),
            "offline": str(options.get("planning_mode") or "online") == "offline",
        }
        request_path = task_dir / "request.json"
        request_path.write_text(
            json.dumps(request_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        db.session.commit()

        runtime = self._resolve_model_runtime(task, user_id)
        self.emit(task, "figure.planning", 35, "正在规划图表结构、视觉编码与双语标签")
        result = self._run_command(task, request_path, runtime)
        payload = _parse_cli_payload(result.stdout)
        if result.returncode != 0:
            self.app.logger.error(
                "Academic figure runtime failed for task %s: %s",
                task.id,
                (result.stderr or result.stdout or "no runtime output")[-4000:],
            )
            raise RuntimeError(_command_error(result, payload))
        if not isinstance(payload.get("spec"), dict) or not isinstance(
            payload.get("quality_report"), dict
        ):
            raise RuntimeError("绘图创作 Agent 返回结果不完整")

        self.emit(task, "figure.rendered", 82, "图表已渲染，正在汇总质量检查与交付文件")
        output = self._collect_output(task_dir, payload)
        record.status = "SUCCEEDED"
        record.figure_spec = output["figure_spec"]
        record.dataset_summary = output["dataset_summary"]
        record.captions = output["figure_captions"]
        record.quality_report = output["figure_quality"]
        record.artifacts = output["artifacts"]
        record.warnings = output["figure_warnings"]
        record.completed_at = datetime.now(UTC)

        task.status = "SUCCEEDED"
        task.finished_at = datetime.now(UTC)
        task.trace_summary = {
            "agent": "academic_figure",
            "planning_mode": record.planning_mode,
            "figure_type": output["figure_spec"].get("figure_type"),
            "quality_passed": output["figure_quality"].get("passed"),
            "artifact_count": len(output["artifacts"]),
        }
        self.merge_output(task, **output)
        self.emit(
            task,
            "task.completed",
            100,
            "学术图表及可复现交付包已生成",
            quality_passed=output["figure_quality"].get("passed"),
        )

    def _record_for(
        self, task: Task, user_id: UUID, options: dict[str, Any]
    ) -> AcademicFigureRun:
        record = self._get_record(task.id, user_id)
        if record is not None:
            return record
        record = AcademicFigureRun(
            task_id=task.id,
            user_id=user_id,
            status="QUEUED",
            planning_mode=str(options.get("planning_mode") or "online"),
            figure_type=str(options.get("figure_type") or "auto"),
            input_files=[],
            options=options,
            figure_spec={},
            dataset_summary={},
            captions={},
            quality_report={},
            artifacts={},
            warnings=[],
        )
        db.session.add(record)
        db.session.flush()
        return record

    @staticmethod
    def _get_record(task_id: UUID, user_id: UUID) -> AcademicFigureRun | None:
        return db.session.scalar(
            select(AcademicFigureRun).where(
                AcademicFigureRun.task_id == task_id,
                AcademicFigureRun.user_id == user_id,
            )
        )

    def _snapshot_inputs(
        self, task: Task, user_id: UUID, task_dir: Path
    ) -> dict[str, Any]:
        inputs_dir = task_dir / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)
        resolved: dict[str, Any] = {"data": [], "context": [], "sketch": [], "metadata": []}
        for index, item in enumerate((task.input_json or {}).get("figure_files") or [], start=1):
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip()
            source = resolve_figure_upload(user_id, item.get("upload_id"), kind)
            if source is None:
                raise RuntimeError("绘图输入文件不存在或不属于当前用户")
            file_name = Path(str(item.get("file_name") or source.name)).name
            destination = inputs_dir / f"{index:02d}-{kind}{source.suffix.lower()}"
            shutil.copy2(source, destination)
            resolved[kind].append(str(destination.resolve()))
            resolved["metadata"].append(
                {
                    "kind": kind,
                    "file_name": file_name,
                    "suffix": source.suffix.lower(),
                    "size": destination.stat().st_size,
                    "path": str(destination.resolve()),
                }
            )
        return resolved

    def _run_command(
        self, task: Task, request_path: Path, runtime: dict[str, object]
    ) -> subprocess.CompletedProcess[str]:
        root = self._runtime_root()
        task_dir = self._task_dir(task)
        existing_pythonpath = os.environ.get("PYTHONPATH", "")
        python_paths = [str(root / "src"), str(root)]
        if existing_pythonpath:
            python_paths.append(existing_pythonpath)
        env = os.environ.copy()
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONPATH": os.pathsep.join(python_paths),
                "MPLCONFIGDIR": str(task_dir / "matplotlib-cache"),
                "ACADEMIC_FIGURE_FONT_PATH": str(self.app.config.get("ACADEMIC_FIGURE_FONT_PATH") or ""),
                "OUTPUT_DIR": str(task_dir / "bundle"),
                "DASHSCOPE_API_KEY": str(runtime["api_key"]),
                "BAILIAN_BASE_URL": str(runtime["base_url"]),
                "BAILIAN_MODEL": str(runtime["model_name"]),
                "BAILIAN_TIMEOUT_SECONDS": str(runtime["timeout_seconds"]),
                "BAILIAN_MAX_RETRIES": str(
                    int(self.app.config["ACADEMIC_FIGURE_MODEL_MAX_RETRIES"])
                ),
                "BAILIAN_ALLOW_OFFLINE_FALLBACK": "true",
            }
        )
        return subprocess.run(
            [sys.executable, str(root / "main.py"), "request", str(request_path)],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=int(self.app.config["ACADEMIC_FIGURE_TIMEOUT_SECONDS"]),
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
            }
        return get_system_model_runtime(user_id)

    def _collect_output(self, task_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
        bundle = (task_dir / "bundle").resolve()
        artifact_definitions = {
            "figure-zh-png": "figure_zh.png",
            "figure-zh-svg": "figure_zh.svg",
            "figure-zh-pdf": "figure_zh.pdf",
            "figure-en-png": "figure_en.png",
            "figure-en-svg": "figure_en.svg",
            "figure-en-pdf": "figure_en.pdf",
            "figure-code-python": "figure.py",
            "figure-code-r": "figure.R",
            "figure-code-latex": "figure.tex",
            "figure-code-mermaid": "figure.mmd",
            "figure-caption-zh": "caption_zh.txt",
            "figure-caption-en": "caption_en.txt",
            "figure-source-data": "source_data.csv",
            "figure-config": "figure_config.json",
            "figure-quality": "quality_report.json",
            "figure-execution": "execution.json",
            "figure-manifest": "manifest.json",
            "figure-request": "request.json",
        }
        artifacts = {
            kind: str((bundle / file_name).resolve())
            for kind, file_name in artifact_definitions.items()
            if (bundle / file_name).is_file()
        }
        return {
            "figure_request": payload.get("request") or {},
            "figure_spec": payload.get("spec") or {},
            "dataset_summary": payload.get("dataset") or {},
            "figure_captions": payload.get("captions") or {},
            "figure_quality": payload.get("quality_report") or {},
            "figure_warnings": payload.get("warnings") or [],
            "figure_artifacts": payload.get("artifacts") or {},
            "artifacts": artifacts,
        }

    def _task_dir(self, task: Task) -> Path:
        root = Path(self.app.config["ACADEMIC_FIGURE_DATA_DIR"]).resolve()
        path = (root / str(task.user_id) / str(task.id)).resolve()
        if not path.is_relative_to(root):
            raise RuntimeError("绘图任务目录越界")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _runtime_root(self) -> Path:
        root = Path(self.app.config["ACADEMIC_FIGURE_RUNTIME_ROOT"]).resolve()
        if not (root / "main.py").is_file() or not (
            root / "src" / "academic_figure_agent" / "__init__.py"
        ).is_file():
            raise RuntimeError(f"绘图创作 Agent 运行时不完整: {root}")
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
    result: subprocess.CompletedProcess[str], payload: dict[str, Any]
) -> str:
    message = str(payload.get("message") or "").strip()
    if message and "Traceback" not in message and "File \"" not in message and len(message) <= 300:
        return message
    return _safe_runtime_error(result.stderr or result.stdout or "")


def _safe_runtime_error(detail: str) -> str:
    normalized = detail.casefold()
    if any(marker in normalized for marker in ("502", "503", "bad gateway", "internalservererror")):
        return "模型服务暂时不可用，在线规划未完成。请重试或选择离线规则规划。"
    if any(marker in normalized for marker in ("timeout", "timed out")):
        return "模型规划请求超时。请重试或选择离线规则规划。"
    if "api key" in normalized or "authentication" in normalized or "401" in normalized:
        return "模型凭证无效或已过期，请在个人中心重新验证模型配置。"
    return "绘图创作运行失败，请检查输入文件后重试。"


def _safe_figure_error(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return "绘图创作运行失败，请稍后重试。"
    if "Traceback" in message or "File \"" in message or "site-packages" in message:
        return _safe_runtime_error(message)
    if len(message) > 300:
        return _safe_runtime_error(message)
    return message
