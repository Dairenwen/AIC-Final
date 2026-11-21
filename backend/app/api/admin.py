from datetime import UTC, datetime

from flask import Blueprint
from sqlalchemy import func, select

from ..extensions import db
from ..models import Agent, ModelConfig, Role, Task, TaskEvent, Tool, User
from .auth import require_role
from .responses import ok

bp = Blueprint("admin", __name__)


@bp.get("/overview")
@bp.get("/system/dashboard")
def overview():
    forbidden = require_role("system_admin")
    if forbidden:
        return forbidden
    return ok(_system_overview())


@bp.get("/system/exceptions")
def system_exceptions():
    forbidden = require_role("system_admin")
    if forbidden:
        return forbidden
    try:
        rows = db.session.scalars(
            select(Task)
            .where(Task.status.in_(["FAILED", "ERROR"]))
            .order_by(Task.updated_at.desc())
            .limit(50)
        ).all()
        items = [
            {
                "id": str(task.id),
                "type": task.task_type,
                "status": task.status,
                "message": task.safe_error_message or task.error_code or "任务执行失败",
                "createdAt": _iso(task.updated_at or task.created_at),
                "retryCount": task.retry_count,
            }
            for task in rows
        ]
    except Exception:
        db.session.rollback()
        items = []
    return ok({"items": items, "total": len(items)})


@bp.get("/system/audit")
def system_audit():
    forbidden = require_role("system_admin")
    if forbidden:
        return forbidden
    try:
        events = db.session.scalars(select(TaskEvent).order_by(TaskEvent.created_at.desc()).limit(80)).all()
        items = [
            {
                "id": str(event.id),
                "resource": "task",
                "resourceId": str(event.task_id),
                "action": event.event_type,
                "detail": event.payload or {},
                "createdAt": _iso(event.created_at),
            }
            for event in events
        ]
    except Exception:
        db.session.rollback()
        items = []
    return ok({"items": items, "total": len(items)})


@bp.get("/system/permissions")
def system_permissions():
    forbidden = require_role("system_admin")
    if forbidden:
        return forbidden
    try:
        roles = db.session.scalars(select(Role).order_by(Role.code)).all()
        role_counts = dict(
            db.session.execute(select(User.role_code, func.count(User.id)).group_by(User.role_code)).all()
        )
        items = [
            {
                "code": role.code,
                "name": role.name,
                "description": role.description or "",
                "status": role.status,
                "userCount": int(role_counts.get(role.code, 0)),
            }
            for role in roles
        ]
    except Exception:
        db.session.rollback()
        items = []
    return ok({"items": items, "total": len(items)})


def _count(model, *criteria) -> int:
    try:
        statement = select(func.count()).select_from(model)
        if criteria:
            statement = statement.where(*criteria)
        return int(db.session.scalar(statement) or 0)
    except Exception:
        db.session.rollback()
        return 0


def _system_overview() -> dict:
    now = datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    users = _count(User, User.deleted_at.is_(None))
    tasks_today = _count(Task, Task.created_at >= today)
    model_configs = _count(ModelConfig, ModelConfig.deleted_at.is_(None))
    failed = _count(Task, Task.status.in_(["FAILED", "ERROR"]))
    return {
        "metrics": [
            {"label": "平台用户", "value": users, "trend": "实时"},
            {"label": "今日任务", "value": tasks_today, "trend": "实时"},
            {"label": "模型配置", "value": model_configs, "trend": "已配置"},
            {"label": "待处理异常", "value": failed, "trend": "需关注" if failed else "正常"},
        ],
        "summary": {
            "activeAgents": _count(Agent, Agent.status == "ACTIVE"),
            "activeTools": _count(Tool, Tool.status == "ACTIVE"),
            "lastRefresh": now.isoformat(),
        },
        "alerts": ([{"level": "warning", "message": f"{failed} 个任务需要处理"}] if failed else []),
        "components": {"api": "up", "database": "up"},
    }


def _iso(value):
    return value.isoformat() if value else None
