from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from ...api.uploads import resolve_paper_upload
from ...extensions import db
from ...models import ModelConfig, PaperReadingRun, Task
from ...services.model_credentials import decrypt_api_key
from ...services.system_model_runtime import get_owned_model_runtime, get_system_model_runtime
from ..task_service import BuiltinAgentTaskService, public_error_message


AGENT_VERSION = "0.6.4"
ARXIV_PATTERN = re.compile(
    r"(?:arxiv(?:\.org/(?:abs|pdf)/)?[:/\s]*)?((?:[a-z-]+(?:\.[A-Z]{2})?/)?\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?",
    re.IGNORECASE,
)


def normalize_paper_reading_options(payload: dict[str, Any]) -> dict[str, Any]:
    speed_profile = str(payload.get("speed_profile") or "balanced").strip().lower()
    if speed_profile not in {"fast", "balanced", "quality"}:
        raise ValueError("请选择有效的论文精读档位")
    question = str(payload.get("follow_up_question") or "").strip()
    if len(question) > 1000:
        raise ValueError("论文内问答不能超过 1000 个字符")
    return {
        "speed_profile": speed_profile,
        "follow_up_question": question,
    }


class PaperReadingService(BuiltinAgentTaskService):
    agent_label = "paper-reading"
    failed_message = "论文精读 Agent 工作流执行失败"

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
                    task.output_json = {
                        **(task.output_json or {}),
                        "task_error": {
                            "kind": type(exc).__name__,
                            "message": safe_error,
                            "diagnostic": str(exc)[-1000:],
                        },
                    }
                    task.finished_at = datetime.now(UTC)
                    if record:
                        record.status = "FAILED"
                    self.emit(task, "task.failed", 100, self.failed_message, error=safe_error)
                self.app.logger.exception("Paper reading task %s failed", task_id)
            finally:
                db.session.remove()

    def run(self, task_id: UUID, user_id: UUID) -> None:
        task = db.session.get(Task, task_id)
        if task is None:
            return

        input_json = task.input_json or {}
        options = input_json.get("paper_reading_options") or {}
        prompt = str(input_json.get("prompt") or "").strip()
        speed_profile = str(options.get("speed_profile") or input_json.get("speed_profile") or "balanced")
        follow_up_question = str(options.get("follow_up_question") or "").strip()
        output_dir = (
            Path(self.app.config["AGENT_GENERATED_DIR"])
            / "paper_reading"
            / str(user_id)
            / str(task.id)
        ).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        source_args, source = self.resolve_source(input_json, user_id)
        record = self._record_for(task, user_id, source, speed_profile)
        task.status = "RUNNING"
        task.started_at = datetime.now(UTC)
        record.status = "RUNNING"
        self.emit(task, "task.started", 5, "已启动论文精读 Agent 0.6.4")

        self.merge_output(task, reading_source=source, paper_reading_agent_version=AGENT_VERSION)
        self.emit(task, "paper.source_ready", 15, "论文来源已确认", source=source)
        self.emit(task, "paper.parsing", 28, "正在解析 PDF 结构、章节与科学对象")

        report_path = output_dir / "report.json"
        markdown_path = output_dir / "report.md"
        timing_path = output_dir / "timing.json"
        model_runtime = self.resolve_model_runtime(task, user_id)
        self.merge_output(
            task,
            paper_reading_runtime={
                "model": str((model_runtime or {}).get("model_name") or get_system_model_runtime(user_id)["model_name"]),
                "base_url": str((model_runtime or {}).get("base_url") or get_system_model_runtime(user_id)["base_url"]),
                "speed_profile": speed_profile,
            },
        )
        self.emit(task, "paper.core_started", 30, "论文精读核心子进程已启动，正在进行文本分析与模型推理")
        result = self.run_core(
            source_args,
            prompt,
            speed_profile,
            report_path,
            markdown_path,
            timing_path,
            model_runtime,
            question=follow_up_question or None,
        )
        self.emit(task, "paper.analysis_ready", 78, "核心精读与可选分析阶段已完成")

        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(report, dict) or report.get("schema_version") != "deep_reading_report_v1":
            raise RuntimeError("论文精读 Agent 返回了不受支持的报告格式")
        markdown = markdown_path.read_text(encoding="utf-8")
        timing = json.loads(timing_path.read_text(encoding="utf-8")) if timing_path.exists() else {}
        reading_result = report.get("reading_result") or {}
        claim_count = len(reading_result.get("claims") or [])
        evidence_count = len(reading_result.get("evidence") or [])
        artifacts = {
            "json": str(report_path),
            "markdown": str(markdown_path),
            "timing": str(timing_path),
        }
        warnings = [
            *(reading_result.get("warnings") or []),
            *((report.get("flow_execution") or {}).get("degradations") or []),
        ]

        record.status = "SUCCEEDED"
        record.paper_metadata = report.get("paper") or {}
        record.reading_result = reading_result
        record.scientific_analysis = {
            "elements": report.get("scientific_elements"),
            "coverage": report.get("scientific_coverage"),
        }
        record.experiment_analysis = {
            "experiments": report.get("experiments"),
            "reproducibility_summary": report.get("reproducibility_summary"),
            "qa_response": report.get("qa_response"),
            "qa_evidence": report.get("qa_evidence") or [],
        }
        record.reliability = report.get("core_reliability") or {}
        record.flow_execution = report.get("flow_execution") or {}
        record.timing = timing if isinstance(timing, dict) else {}
        record.artifacts = artifacts
        record.warnings = warnings
        record.completed_at = datetime.now(UTC)

        self.merge_output(
            task,
            paper_reading=report,
            paper_reading_summary={
                "agent_version": AGENT_VERSION,
                "claim_count": claim_count,
                "evidence_count": evidence_count,
                "scientific_element_count": len(
                    (report.get("scientific_elements") or {}).get("elements") or []
                ),
                "reliability_record_count": len(
                    (report.get("core_reliability") or {}).get("records") or []
                ),
            },
            report_markdown=markdown,
            timing=timing,
            artifacts=artifacts,
            logs={"stderr": result.stderr[-3000:], "returncode": result.returncode},
        )
        self.emit(
            task,
            "paper.evidence_validated",
            92,
            f"已校验 {claim_count} 条结论与 {evidence_count} 条证据",
            claim_count=claim_count,
            evidence_count=evidence_count,
        )

        task.status = "SUCCEEDED"
        task.progress = 100
        task.current_step = "论文精读完成"
        task.finished_at = datetime.now(UTC)
        task.trace_summary = {
            "agent": "paper_reading",
            "agent_version": AGENT_VERSION,
            "claim_count": claim_count,
            "evidence_count": evidence_count,
            "completion_status": (report.get("flow_execution") or {}).get("completion_status"),
        }
        db.session.commit()
        self.emit(task, "task.completed", 100, "论文精读任务已完成")

    def resolve_source(self, input_json: dict[str, Any], user_id: UUID) -> tuple[list[str], dict[str, Any]]:
        upload_id = input_json.get("attachment_id")
        if upload_id:
            path = resolve_paper_upload(user_id, upload_id)
            if path is None:
                raise ValueError("上传的 PDF 不存在或不属于当前用户")
            return [str(path)], {
                "type": "USER_UPLOAD",
                "uploadId": str(upload_id),
                "fileName": str(input_json.get("attachment") or "paper.pdf"),
            }

        arxiv_id = extract_arxiv_id(str(input_json.get("link") or ""))
        if arxiv_id:
            return ["--arxiv-id", arxiv_id], {"type": "ARXIV", "arxivId": arxiv_id}
        raise ValueError("请上传 PDF 或提供有效的 arXiv 链接")

    def run_core(
        self,
        source_args: list[str],
        prompt: str,
        speed_profile: str,
        report_path: Path,
        markdown_path: Path,
        timing_path: Path,
        model_runtime: dict[str, object] | None = None,
        *,
        question: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if speed_profile not in {"fast", "balanced", "quality"}:
            speed_profile = "balanced"
        root = Path(self.app.config["PAPER_READING_RUNTIME_ROOT"]).resolve()
        script = root / "scripts" / "run_real_pdf_agent.py"
        project = root / "agent-core"
        version_file = root / "VERSION"
        contract_schema = root / "shared" / "contracts" / "schemas" / "reading_result.schema.json"
        uv_executable = _resolve_uv_executable(self.app.config["PAPER_READING_UV_EXECUTABLE"])
        if (
            uv_executable is None
            or not script.is_file()
            or not project.is_dir()
            or not version_file.is_file()
            or not contract_schema.is_file()
            or version_file.read_text(encoding="utf-8").strip() != AGENT_VERSION
        ):
            raise RuntimeError("论文精读 Agent 0.6.4 系统内运行时不完整")

        runtime = model_runtime or get_system_model_runtime(task.user_id if hasattr(task, "user_id") else UUID(int=0))
        credential_env = "ZHIYAN_PAPER_READING_API_KEY"
        uv_cache_dir = _writable_uv_cache_dir(self.app.config["PAPER_READING_UV_CACHE_DIR"])
        env = os.environ.copy()
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "UV_CACHE_DIR": str(uv_cache_dir),
                credential_env: str(runtime["api_key"]),
                "PAPER_READING_ENABLE_THINKING": "false",
                "PAPER_READING_VISION_ENABLE_THINKING": "false",
                # Do not enable Hugging Face's optional Xet transport on the
                # text-first path used by production deployments.
                "HF_HUB_DISABLE_XET": "1",
            }
        )
        enable_docling = bool(self.app.config.get("PAPER_READING_ENABLE_DOCLING", False))
        command = [
            uv_executable,
            "run",
            "--project",
            str(project),
            "--frozen",
        ]
        if enable_docling:
            command.extend(["--extra", "tables"])
        command.extend(
            [
                "--exact",
                "--no-managed-python",
                "--python",
                os.path.realpath(os.sys.executable),
                "python",
                str(script),
                *source_args,
                "--goal",
                prompt,
                "--speed-profile",
                speed_profile,
                "--execution-mode",
                "flow_first",
                "--strategy",
                "section_parent_child_v1",
                "--language",
                "zh-CN",
                "--model-base-url",
                str(runtime["base_url"]),
                "--model",
                str(runtime["model_name"]),
                "--credential-env",
                credential_env,
                "--timeout-seconds",
                str(runtime["timeout_seconds"]),
                "--json-output",
                str(report_path),
                "--markdown-output",
                str(markdown_path),
                "--timing-json-output",
                str(timing_path),
            ]
        )
        if question:
            command.extend(["--question", question])
        command.append(
            "--table-structure" if enable_docling else "--no-table-structure"
        )
        result = subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=int(self.app.config["PAPER_READING_TIMEOUT_SECONDS"]),
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "论文精读核心工作流执行失败").strip()
            raise RuntimeError(_safe_core_error(message))
        if not report_path.is_file() or not markdown_path.is_file():
            raise RuntimeError("论文精读 Agent 未生成预期报告")
        return result

    def resolve_model_runtime(self, task: Task, user_id: UUID) -> dict[str, object] | None:
        if task.model_config_id is None:
            return get_system_model_runtime(user_id)
        item = db.session.get(ModelConfig, task.model_config_id)
        if item is None:
            raise RuntimeError("所选个人模型不存在、未验证或已停用")
        return get_owned_model_runtime(item, user_id)

    def _record_for(
        self,
        task: Task,
        user_id: UUID,
        source: dict[str, Any],
        speed_profile: str,
    ) -> PaperReadingRun:
        record = self._get_record(task.id, user_id)
        if record is not None:
            return record
        record = PaperReadingRun(
            task_id=task.id,
            user_id=user_id,
            agent_version=AGENT_VERSION,
            status="QUEUED",
            source_type=str(source.get("type") or "UNKNOWN"),
            source_metadata=source,
            speed_profile=speed_profile,
            execution_mode="flow_first",
            paper_metadata={},
            reading_result={},
            scientific_analysis={},
            experiment_analysis={},
            reliability={},
            flow_execution={},
            timing={},
            artifacts={},
            warnings=[],
        )
        db.session.add(record)
        db.session.flush()
        return record

    @staticmethod
    def _get_record(task_id: UUID, user_id: UUID) -> PaperReadingRun | None:
        return db.session.scalar(
            select(PaperReadingRun).where(
                PaperReadingRun.task_id == task_id,
                PaperReadingRun.user_id == user_id,
            )
        )


def extract_arxiv_id(value: str) -> str | None:
    match = ARXIV_PATTERN.search(value.strip())
    return match.group(1) if match else None


def _safe_core_error(message: str) -> str:
    lowered = message.casefold()
    if "hf-xet" in lowered or "huggingface-hub" in lowered:
        return (
            "论文精读检测到可选 Docling/Hugging Face 依赖安装失败；"
            "基础文本精读已配置为跳过该依赖，请重启服务后重试"
        )
    if "shapely" in lowered or "docling" in lowered or "uv" in lowered:
        lines = [line.strip() for line in message.splitlines() if line.strip()]
        detail = " ".join(lines[-4:])[:700]
        return f"论文精读运行时依赖安装失败（Docling/Shapely/uv）：{detail}"
    if "ReadTimeout" in message or "timed out" in message.lower():
        return "论文精读模型响应超时，请稍后重试或选择响应更快的个人模型"
    if "MODEL_ANALYSIS_FAILED" in message or "valid reading analysis" in message:
        return "当前模型未返回符合论文精读契约的结构化结果，请更换兼容模型后重试"
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    return (lines[-1] if lines else "论文精读核心工作流执行失败")[:500]


def _writable_uv_cache_dir(configured: str | Path) -> Path:
    """Use a writable uv cache even when an old deployment path is invalid."""
    candidates = [
        Path(configured),
        Path(tempfile.gettempdir()) / "zhiyan-uv-paper-reading-cache",
    ]
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
            resolved.mkdir(parents=True, exist_ok=True)
            probe = resolved / ".write-test"
            probe.write_text("ok", encoding="ascii")
            probe.unlink()
            return resolved
        except OSError:
            continue
    raise RuntimeError("论文精读运行缓存目录不可写，请配置 PAPER_READING_UV_CACHE_DIR")


def _resolve_uv_executable(configured: object) -> str | None:
    value = str(configured or "uv").strip() or "uv"
    candidates = [value] if Path(value).is_absolute() else [value]
    candidates.extend(("/usr/local/bin/uv", "/snap/bin/uv"))
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None
