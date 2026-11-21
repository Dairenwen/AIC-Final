from __future__ import annotations

import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from ..extensions import db
from ..models import PaperReadingRun, PatentDraftingRun, Task, TaskEvent


def _process_start(pid: int) -> str:
    # Linux /proc stat has a parenthesized command which may contain spaces.
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def _boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def current_worker() -> dict[str, Any] | None:
    try:
        return {
            "host": socket.gethostname(),
            "boot": _boot_id(),
            "pid": os.getpid(),
            "start": _process_start(os.getpid()),
        }
    except (OSError, IndexError, ValueError):
        return None


def worker_is_gone(marker: Any) -> bool:
    if not isinstance(marker, dict) or marker.get("host") != socket.gethostname():
        return False
    try:
        pid = int(marker["pid"])
        start = str(marker["start"])
        boot = _boot_id()
        if pid <= 0 or not start or not marker.get("boot"):
            return False
        if marker["boot"] != boot:
            return True
        try:
            return _process_start(pid) != start
        except FileNotFoundError:
            return True
    except (OSError, KeyError, TypeError, ValueError, IndexError):
        # When ownership cannot be established, never interrupt a task.
        return False


def register_task_worker(task_id: UUID) -> None:
    marker = current_worker()
    if marker is None:
        return
    task = db.session.get(Task, task_id)
    if task is not None:
        task.trace_summary = {**(task.trace_summary or {}), "_worker": marker}
        db.session.commit()


def mark_interrupted(task: Task) -> None:
    message = "任务执行进程已中断，请重新提交任务"
    task.status = "FAILED"
    task.progress = 100
    task.current_step = message
    task.error_code = "WORKER_INTERRUPTED"
    task.safe_error_message = message
    task.finished_at = datetime.now(UTC)
    task.output_json = {
        **(task.output_json or {}),
        "task_error": {"kind": "worker_interrupted", "message": message, "retryable": True},
    }
    for model in (PaperReadingRun, PatentDraftingRun):
        run = db.session.scalar(select(model).where(model.task_id == task.id))
        if run is not None and run.status in {"QUEUED", "RUNNING"}:
            run.status = "FAILED"
    sequence = db.session.scalar(
        select(func.coalesce(func.max(TaskEvent.sequence), 0)).where(TaskEvent.task_id == task.id)
    )
    db.session.add(TaskEvent(
        task_id=task.id,
        sequence=int(sequence or 0) + 1,
        event_type="task.failed",
        payload={"progress": 100, "message": message, "error": message},
    ))


def reconcile_interrupted_task(task: Task) -> bool:
    if task.status not in {"QUEUED", "RUNNING"} or not worker_is_gone((task.trace_summary or {}).get("_worker")):
        return False
    # Lock and reread before changing the status, so concurrent requests do
    # not append duplicate events or override a task that just completed.
    locked = db.session.scalar(
        select(Task).where(Task.id == task.id).with_for_update().execution_options(populate_existing=True)
    )
    if locked is None or locked.status not in {"QUEUED", "RUNNING"}:
        return False
    if not worker_is_gone((locked.trace_summary or {}).get("_worker")):
        return False
    mark_interrupted(locked)
    db.session.commit()
    return True
