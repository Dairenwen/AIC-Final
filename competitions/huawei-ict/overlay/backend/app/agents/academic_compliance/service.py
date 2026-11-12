from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from ...api.uploads import resolve_manuscript_upload
from ...extensions import db
from ...models import ModelConfig, Task
from ...services.model_credentials import decrypt_api_key
from ..task_service import BuiltinAgentTaskService


class AcademicComplianceService(BuiltinAgentTaskService):
    agent_label = "academic-compliance"
    failed_message = "学术合规性检测 Agent 工作流执行失败"

    def run(self, task_id: UUID, user_id: UUID) -> None:
        task = db.session.get(Task, task_id)
        if task is None:
            return
        input_json = task.input_json or {}
        manuscript_path = resolve_manuscript_upload(user_id, input_json.get("attachment_id"))
        if manuscript_path is None:
            raise ValueError("上传的稿件不存在或不属于当前用户")
        options = normalize_compliance_options(input_json.get("compliance_options"))

        output_dir = (
            Path(self.app.config["AGENT_GENERATED_DIR"])
            / "academic_compliance"
            / str(task.id)
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        task.status = "RUNNING"
        task.started_at = datetime.now(UTC)
        self.emit(task, "task.started", 5, "已启动学术合规性检测 Agent")
        self.merge_output(
            task,
            compliance_request={
                "file_name": str(input_json.get("attachment") or manuscript_path.name),
                "file_type": manuscript_path.suffix.removeprefix("."),
                **options,
            },
        )
        self.emit(task, "compliance.source_ready", 14, "稿件来源和用户权限已确认")
        self.emit(task, "compliance.parsing", 25, "正在解析论文结构、引用、图表和声明")
        self.emit(task, "compliance.rules_ready", 36, "学术规范与投稿规则库已加载")
        self.emit(task, "compliance.checks_started", 48, "正在执行四类合规检查")

        model_runtime = self.resolve_model_runtime(task, user_id)
        result = self.run_core(
            manuscript_path,
            output_dir,
            user_id,
            task.id,
            options,
            model_runtime,
        )
        payload, report_path, json_path = read_compliance_artifacts(result.stdout, output_dir)
        summary = payload.get("summary") or {}
        compliance_summary = payload.get("compliance_summary") or {}
        risks = payload.get("risks") or []
        suggestions = payload.get("suggestions") or []
        module_results = payload.get("module_check_results") or {}
        report_markdown = report_path.read_text(encoding="utf-8")

        self.merge_output(
            task,
            academic_compliance=payload,
            compliance_summary=compliance_summary,
            risk_summary=summary,
            risks=risks,
            suggestions=suggestions,
            module_check_results=module_results,
            report_markdown=report_markdown,
            artifacts={"report_markdown": str(report_path), "result_json": str(json_path)},
            logs={
                "stdout": result.stdout[-3000:],
                "stderr": result.stderr[-3000:],
                "returncode": result.returncode,
            },
        )
        self.emit(
            task,
            "compliance.checks_ready",
            76,
            f"四类检查完成，共发现 {len(risks)} 项风险",
            risk_count=len(risks),
        )
        self.emit(
            task,
            "compliance.summary_ready",
            90,
            f"合规得分 {compliance_summary.get('compliance_score', 0)}，报告已生成",
            compliance_score=compliance_summary.get("compliance_score", 0),
        )

        task.status = "SUCCEEDED"
        task.progress = 100
        task.current_step = "学术合规性检测完成"
        task.finished_at = datetime.now(UTC)
        task.trace_summary = {
            "agent": "academic_compliance",
            "runtime": "academic_compliance_agent",
            "compliance_score": compliance_summary.get("compliance_score", 0),
            "overall_level": summary.get("overall_level", "极低"),
            "risk_count": len(risks),
            "model": model_runtime["model_name"] if model_runtime else self.app.config["QWEN_DPO_MODEL"],
        }
        db.session.commit()
        self.emit(task, "task.completed", 100, "学术合规性检测任务已完成")

    def run_core(
        self,
        manuscript_path: Path,
        output_dir: Path,
        user_id: UUID,
        thread_id: UUID,
        options: dict[str, str],
        model_runtime: dict[str, object] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        root = Path(self.app.config["COMPLIANCE_AGENT_ROOT"])
        entry = root / "main.py"
        if not root.is_dir() or not entry.is_file() or not (root / "app" / "graph").is_dir():
            raise RuntimeError("学术合规性检测 Agent 运行目录未正确配置")
        offline = not model_runtime and not self.app.config.get("PLATFORM_MODEL_ENABLED", False)
        runtime = model_runtime or ({
            "base_url": "", "model_name": "", "api_key": "",
            "timeout_seconds": float(self.app.config["QWEN_DPO_TIMEOUT_SECONDS"]),
        } if offline else {
            "base_url": str(self.app.config["QWEN_DPO_BASE_URL"]),
            "model_name": str(self.app.config["QWEN_DPO_MODEL"]),
            "api_key": str(self.app.config["QWEN_DPO_API_KEY"]),
            "timeout_seconds": float(self.app.config["QWEN_DPO_TIMEOUT_SECONDS"]),
        })
        env = os.environ.copy()
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "OPENAI_BASE_URL": str(runtime["base_url"]),
                "OPENAI_MODEL": str(runtime["model_name"]),
                "OPENAI_API_KEY": str(runtime["api_key"]),
                "COMPLIANCE_AGENT_USE_LLM": (
                    "true" if self.app.config["COMPLIANCE_AGENT_USE_LLM"] and not offline else "false"
                ),
                "COMPLIANCE_AGENT_LLM_TIMEOUT": str(
                    max(1, int(float(runtime["timeout_seconds"])))
                ),
                "COMPLIANCE_AGENT_MEMORY_ENABLED": (
                    "true" if self.app.config["COMPLIANCE_AGENT_MEMORY_ENABLED"] else "false"
                ),
            }
        )
        command = [
            sys.executable,
            str(entry),
            "--input",
            str(manuscript_path),
            "--user-id",
            str(user_id),
            "--thread-id",
            str(thread_id),
            "--task-type",
            options["task_type"],
            "--target-rule-set",
            options["target_rule_set"],
            "--output-dir",
            str(output_dir),
        ]
        result = subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=int(self.app.config["COMPLIANCE_AGENT_TIMEOUT_SECONDS"]),
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "学术合规性检测核心工作流执行失败").strip()
            raise RuntimeError(message[-1500:])
        return result

    def resolve_model_runtime(self, task: Task, user_id: UUID) -> dict[str, object] | None:
        if task.model_config_id is None:
            return None
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
            "api_key": decrypt_api_key(item.encrypted_api_key, item.key_nonce, item.key_version),
            "timeout_seconds": max(10, min(float(settings.get("timeout_seconds", 120)), 600)),
        }


def normalize_compliance_options(value: object) -> dict[str, str]:
    options = value if isinstance(value, dict) else {}
    task_type = str(options.get("task_type") or "paper_precheck")
    if task_type not in {"paper_precheck", "journal_submission"}:
        task_type = "paper_precheck"
    target_rule_set = str(options.get("target_rule_set") or "default").strip()[:64] or "default"
    return {"task_type": task_type, "target_rule_set": target_rule_set}


def read_compliance_artifacts(stdout: str, output_dir: Path) -> tuple[dict[str, Any], Path, Path]:
    output_root = output_dir.resolve()
    report_path = _path_from_stdout(stdout, "Report:", output_root)
    json_path = _path_from_stdout(stdout, "JSON:", output_root)
    if report_path is None or not report_path.is_file():
        reports = sorted(output_dir.glob("*_report.md"), key=lambda path: path.stat().st_mtime, reverse=True)
        report_path = reports[0].resolve() if reports else None
    if json_path is None or not json_path.is_file():
        results = sorted(output_dir.glob("*_result.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        json_path = results[0].resolve() if results else None
    if report_path is None or json_path is None:
        raise RuntimeError("学术合规性检测 Agent 未生成预期报告")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    payload = normalize_compliance_payload(payload)
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("summary"), dict)
        or not isinstance(payload.get("compliance_summary"), dict)
        or not isinstance(payload.get("risks"), list)
        or not isinstance(payload.get("module_check_results"), dict)
    ):
        raise RuntimeError("学术合规性检测 Agent 输出格式异常")
    return payload, report_path, json_path


def normalize_compliance_payload(payload: object) -> dict[str, Any]:
    """Accept both current and legacy runtime JSON envelopes.

    Older server workers wrote the LangGraph ``structured_output`` object
    directly, while the current Web contract expects its fields at the root.
    Normalize both forms before the result is persisted for the frontend.
    """
    if not isinstance(payload, dict):
        return {}
    legacy = isinstance(payload.get("structured_output"), dict)
    nested = payload.get("structured_output")
    if isinstance(nested, dict):
        merged = {**nested, **payload}
        for key in ("summary", "compliance_summary", "module_check_results"):
            if key not in merged and key in nested:
                merged[key] = nested[key]
        payload = merged
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        summary = payload.get("risk_summary") if isinstance(payload.get("risk_summary"), dict) else {}
        payload["summary"] = summary
    compliance = payload.get("compliance_summary")
    if not isinstance(compliance, dict):
        compliance = payload.get("compliance") if isinstance(payload.get("compliance"), dict) else {}
        payload["compliance_summary"] = compliance
    if "compliance_score" not in compliance:
        for candidate in ("score", "overall_score", "complianceScore"):
            if candidate in compliance:
                compliance["compliance_score"] = compliance[candidate]
                break
    risks = payload.get("risks")
    if not isinstance(risks, list):
        payload["risks"] = payload.get("risk_items") if isinstance(payload.get("risk_items"), list) else []
    modules = payload.get("module_check_results")
    if not isinstance(modules, dict):
        payload["module_check_results"] = payload.get("modules") if isinstance(payload.get("modules"), dict) else {}
    suggestions = payload.get("suggestions")
    if legacy and not isinstance(suggestions, list):
        payload["suggestions"] = compliance.get("revision_suggestions", []) if isinstance(compliance.get("revision_suggestions"), list) else []
    return payload


def _path_from_stdout(stdout: str, prefix: str, output_root: Path) -> Path | None:
    for line in stdout.splitlines():
        if line.startswith(prefix):
            candidate = Path(line.split(":", 1)[1].strip()).resolve()
            if candidate.is_file() and candidate.is_relative_to(output_root):
                return candidate
    return None
