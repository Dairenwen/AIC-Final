from __future__ import annotations

import threading
import re
import subprocess
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from flask import Flask
from sqlalchemy import func, select

from ..extensions import db
from ..models import Task, TaskEvent


TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELED"}


class BuiltinAgentTaskService:
    """Small shared runner for built-in task-style agents."""

    agent_label = "Agent"
    failed_message = "Agent 工作流执行失败"

    def __init__(self, app: Flask) -> None:
        self.app = app

    def start(self, task_id: UUID, user_id: UUID) -> None:
        thread = threading.Thread(
            target=self._run_with_context,
            args=(task_id, user_id),
            name=f"{self.agent_label}-{task_id}",
            daemon=True,
        )
        thread.start()

    def _run_with_context(self, task_id: UUID, user_id: UUID) -> None:
        with self.app.app_context():
            try:
                self.run(task_id, user_id)
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                task = db.session.get(Task, task_id)
                if task:
                    safe_error = public_error_message(exc, self.failed_message)
                    error_detail = classify_task_error(exc, safe_error)
                    task.status = "FAILED"
                    task.progress = 100
                    task.error_code = type(exc).__name__
                    task.safe_error_message = safe_error
                    task.output_json = {
                        **(task.output_json or {}),
                        "task_error": error_detail,
                    }
                    task.trace_summary = {
                        **(task.trace_summary or {}),
                        "error_kind": error_detail["kind"],
                    }
                    task.finished_at = datetime.now(UTC)
                    self.emit(
                        task,
                        "task.failed",
                        100,
                        self.failed_message,
                        error=safe_error,
                        error_detail=error_detail,
                        error_kind=error_detail["kind"],
                        feedback_kind="failure",
                        feedback_level="error",
                        task_phase="failed",
                    )
                self.app.logger.exception("%s task %s failed", self.agent_label, task_id)
            finally:
                db.session.remove()

    def run(self, task_id: UUID, user_id: UUID) -> None:
        raise NotImplementedError

    def emit(self, task: Task, event_type: str, progress: int, message: str, **payload: Any) -> None:
        sequence = db.session.scalar(
            select(func.coalesce(func.max(TaskEvent.sequence), 0)).where(TaskEvent.task_id == task.id)
        )
        task.progress = min(max(progress, 0), 100)
        task.current_step = message[:150]
        db.session.add(
            TaskEvent(
                task_id=task.id,
                sequence=int(sequence or 0) + 1,
                event_type=event_type,
                payload={"progress": task.progress, "message": message, **to_jsonable(payload)},
            )
        )
        db.session.commit()

    @staticmethod
    def merge_output(task: Task, **updates: Any) -> None:
        task.output_json = {**(task.output_json or {}), **to_jsonable(updates)}


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (UUID, datetime)):
        return str(value)
    return value


def public_error_message(exc: Exception, fallback: str = "智能体工作流执行失败") -> str:
    """Return a useful user-facing error without stack traces or local paths."""
    message = str(exc).strip()
    lowered = message.lower()
    if isinstance(exc, TimeoutError) or "timeoutexpired" in lowered or "timed out" in lowered:
        return "智能体执行超时，请稍后重试或缩小任务范围"
    if any(token in lowered for token in ("connection refused", "connecterror", "connectionerror")):
        return "智能体依赖服务暂时不可用，请稍后重试"
    if "filenotfounderror" in lowered or "no such file or directory" in lowered:
        return "智能体运行文件或输出路径不可用，请联系管理员"
    if "validationerror" in lowered or "did not return a valid" in lowered:
        return "智能体返回结果格式不符合要求，请稍后重试"

    unsafe = (
        "traceback" in lowered
        or "site-packages" in lowered
        or bool(re.search(r"(?:[A-Za-z]:\\|/[A-Za-z0-9_.-]+/)", message))
        or "\n" in message
    )
    if not unsafe and message:
        return message[:500]
    return fallback


def classify_task_error(exc: Exception, safe_message: str) -> dict[str, Any]:
    raw_message = str(exc).strip()
    lowered = raw_message.lower()
    kind = "runtime_error"
    next_action = "请稍后重试；如果问题持续存在，请联系管理员检查智能体日志"

    if isinstance(exc, ValueError):
        kind = "input_error"
        next_action = "请检查输入文件、语言方向和任务参数后重新提交"
    elif isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) or "timed out" in lowered:
        kind = "timeout"
        next_action = "请缩小任务范围、关闭高成本选项后重试"
    elif any(
        token in lowered
        for token in (
            "运行目录未正确配置",
            "需要配置",
            "未正确配置",
            "config",
            "配置",
        )
    ):
        kind = "configuration_error"
        next_action = "请联系管理员检查翻译运行时和相关环境变量配置"
    elif any(
        token in lowered
        for token in (
            "未返回有效 json",
            "未生成可用译文文件",
            "输出路径",
            "结果格式",
            "artifact",
        )
    ):
        kind = "artifact_error"
        next_action = "请重新执行任务；如果持续失败，请排查翻译 Agent 输出契约"
    elif any(token in lowered for token in ("connection refused", "connecterror", "connectionerror")):
        kind = "dependency_error"
        next_action = "请稍后重试；如果持续失败，请联系管理员检查依赖服务状态"

    return {
        "kind": kind,
        "message": safe_message,
        "retryable": kind not in {"input_error", "configuration_error"},
        "next_action": next_action,
    }
