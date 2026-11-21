from __future__ import annotations

import csv
from io import StringIO
from typing import Any

from knowledge_base_runtime.backend.config.settings import KB_SHARED_USER_SCHEMA
from knowledge_base_runtime.backend.dao.database import get_db, utc_now
from knowledge_base_runtime.backend.utils.common import dumps, loads_dict


OPERATE_TYPES = {
    "PAPER_INGEST",
    "CHUNK",
    "VECTOR_SYNC",
    "METADATA_CHANGE",
    "QA",
    "AGENT",
    "SYSTEM_PERMISSION",
}

ACTION_TO_OPERATION = {
    "INGEST_METADATA": ("PAPER_INGEST", "CRAWLER_METADATA_INGEST"),
    "UPLOAD_PDF": ("PAPER_INGEST", "PDF_AUTO_PARSE"),
    "UPSERT_USER_PAPER": ("PAPER_INGEST", "PAPER_CREATE"),
    "SLICE": ("CHUNK", "MANUAL_CHUNK"),
    "MODIFY": ("METADATA_CHANGE", "METADATA_UPDATE"),
    "DELETE": ("METADATA_CHANGE", "ARCHIVE_DELETE"),
    "RETRY_PARSE": ("SYSTEM_PERMISSION", "EXCEPTION_RETRY"),
    "INVOKE_AGENT": ("AGENT", "AGENT_INVOKE"),
}


def record_audit_log(
    db: Any,
    *,
    operate_user_id: str | None = None,
    operate_name: str | None = None,
    user_ip: str | None = None,
    operate_type: str | None = None,
    operate_sub_type: str | None = None,
    target_resource_type: str | None = None,
    target_resource_id: str | None = None,
    resource_title: str | None = None,
    operate_content: dict[str, Any] | str | None = None,
    is_system_op: bool = False,
    user_id: str | None = None,
    action: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    detail: dict[str, Any] | str | None = None,
    ip: str | None = None,
) -> None:
    """Write one audit row using the Feishu audit-log field model.

    The legacy keyword arguments are accepted only so older service callsites
    can be migrated incrementally without losing audit data.
    """
    if action and (not operate_type or not operate_sub_type):
        mapped_type, mapped_sub_type = ACTION_TO_OPERATION.get(action, ("SYSTEM_PERMISSION", action))
        operate_type = operate_type or mapped_type
        operate_sub_type = operate_sub_type or mapped_sub_type
    operate_type = _normalize_operate_type(operate_type)
    operate_user_id = operate_user_id or user_id or ("system" if is_system_op else None)
    operate_name = operate_name or _resolve_operate_name(db, operate_user_id, is_system_op)
    target_resource_type = target_resource_type or object_type
    target_resource_id = target_resource_id or object_id
    user_ip = user_ip or ip or ("127.0.0.1" if is_system_op else "-")
    content = operate_content if operate_content is not None else detail
    content_text = content if isinstance(content, str) else dumps(content or {})

    db.execute(
        """
        INSERT INTO audit_logs(
            operate_time,
            operate_user_id,
            operate_name,
            user_ip,
            operate_type,
            operate_sub_type,
            target_resource_type,
            target_resource_id,
            resource_title,
            operate_content,
            is_system_op
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now(),
            operate_user_id,
            operate_name,
            user_ip,
            operate_type,
            operate_sub_type or "UNKNOWN",
            target_resource_type,
            target_resource_id,
            resource_title,
            content_text,
            bool(is_system_op),
        ),
    )


def list_audit_logs(
    *,
    operate_type: str | None = None,
    operate_sub_type: str | None = None,
    target_resource_type: str | None = None,
    operate_user_id: str | None = None,
    keyword: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    page: int = 1,
    size: int = 20,
    action: str | None = None,
    object_type: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    page = max(page, 1)
    size = min(max(size, 1), 200)
    offset = (page - 1) * size
    if action and not operate_type:
        operate_type = ACTION_TO_OPERATION.get(action, (action, ""))[0]
    if object_type and not target_resource_type:
        target_resource_type = object_type
    operate_user_id = operate_user_id or user_id
    where, params = _audit_where(
        operate_type,
        operate_sub_type,
        target_resource_type,
        operate_user_id,
        keyword,
        start_time,
        end_time,
    )

    with get_db() as db:
        total = db.execute(f"SELECT COUNT(*) AS c FROM audit_logs WHERE {where}", params).fetchone()["c"]
        rows = db.execute(
            f"""
            SELECT *
            FROM audit_logs
            WHERE {where}
            ORDER BY operate_time DESC, log_id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, size, offset],
        ).fetchall()

    items = [_serialize_audit_row(dict(row)) for row in rows]
    return {"total": total, "page": page, "size": size, "list": items}


def get_audit_stats() -> dict[str, Any]:
    with get_db() as db:
        total = db.execute("SELECT COUNT(*) AS c FROM audit_logs").fetchone()["c"]
        users = db.execute(
            """
            SELECT COUNT(DISTINCT operate_user_id) AS c
            FROM audit_logs
            WHERE operate_user_id IS NOT NULL AND operate_user_id != ''
            """
        ).fetchone()["c"]
        actions = [
            dict(row)
            for row in db.execute(
                """
                SELECT operate_type, COUNT(*) AS count
                FROM audit_logs
                GROUP BY operate_type
                ORDER BY count DESC, operate_type
                """
            ).fetchall()
        ]
        resources = [
            dict(row)
            for row in db.execute(
                """
                SELECT COALESCE(target_resource_type, '-') AS target_resource_type, COUNT(*) AS count
                FROM audit_logs
                GROUP BY COALESCE(target_resource_type, '-')
                ORDER BY count DESC, target_resource_type
                """
            ).fetchall()
        ]
        recent = [
            dict(row)
            for row in db.execute(
                """
                SELECT substr(operate_time, 1, 10) AS date, COUNT(*) AS count
                FROM audit_logs
                GROUP BY substr(operate_time, 1, 10)
                ORDER BY date DESC
                LIMIT 14
                """
            ).fetchall()
        ]
    return {
        "total": total,
        "active_users": users,
        "actions": actions,
        "resources": resources,
        "recent": list(reversed(recent)),
        "operate_types": sorted(OPERATE_TYPES),
    }


def export_audit_logs_csv(**filters: Any) -> str:
    logs = list_audit_logs(page=1, size=200, **filters)["list"]
    output = StringIO()
    writer = csv.writer(output)
    fields = [
        "log_id",
        "operate_time",
        "operate_user_id",
        "operate_name",
        "user_ip",
        "operate_type",
        "operate_sub_type",
        "target_resource_type",
        "target_resource_id",
        "resource_title",
        "operate_content",
        "is_system_op",
    ]
    writer.writerow(fields)
    for log in logs:
        writer.writerow([log.get(key) for key in fields])
    return output.getvalue()


def _audit_where(
    operate_type: str | None,
    operate_sub_type: str | None,
    target_resource_type: str | None,
    operate_user_id: str | None,
    keyword: str | None,
    start_time: str | None,
    end_time: str | None,
) -> tuple[str, list[Any]]:
    where = ["1=1"]
    params: list[Any] = []
    if operate_type:
        where.append("operate_type = ?")
        params.append(operate_type)
    if operate_sub_type:
        where.append("operate_sub_type = ?")
        params.append(operate_sub_type)
    if target_resource_type:
        where.append("target_resource_type = ?")
        params.append(target_resource_type)
    if operate_user_id:
        where.append("operate_user_id = ?")
        params.append(operate_user_id)
    if start_time:
        where.append("operate_time >= ?")
        params.append(start_time)
    if end_time:
        where.append("operate_time <= ?")
        params.append(end_time)
    if keyword:
        like = f"%{keyword}%"
        where.append(
            """
            (
                operate_type LIKE ?
                OR operate_sub_type LIKE ?
                OR target_resource_type LIKE ?
                OR target_resource_id LIKE ?
                OR resource_title LIKE ?
                OR operate_content LIKE ?
                OR operate_user_id LIKE ?
                OR operate_name LIKE ?
            )
            """
        )
        params.extend([like, like, like, like, like, like, like, like])
    return " AND ".join(where), params


def _serialize_audit_row(row: dict[str, Any]) -> dict[str, Any]:
    content_json = loads_dict(row.get("operate_content"))
    row["operate_content_json"] = content_json
    row["operate_content_text"] = _format_detail(content_json, row.get("operate_content"))
    row["is_system_op"] = bool(row.get("is_system_op"))
    # Backward-compatible aliases for older frontends or scripts.
    row["id"] = row.get("log_id")
    row["timestamp"] = row.get("operate_time")
    row["user_ip"] = row.get("user_ip") or "-"
    row["action"] = row.get("operate_type")
    row["action_type"] = row.get("operate_type")
    row["object_type"] = row.get("target_resource_type")
    row["object_id"] = row.get("target_resource_id")
    resource = row.get("target_resource_type") or "-"
    if row.get("resource_title"):
        resource = f"{resource} / {row['resource_title']}"
    elif row.get("target_resource_id"):
        resource = f"{resource} / {row['target_resource_id']}"
    row["resource"] = resource
    row["detail"] = row.get("operate_content")
    row["detail_text"] = row["operate_content_text"]
    return row


def _resolve_operate_name(db: Any, operate_user_id: str | None, is_system_op: bool) -> str:
    if is_system_op:
        return "系统后台任务"
    if not operate_user_id:
        return "未知用户"
    try:
        row = db.execute(
            "SELECT username FROM service_accounts WHERE username = ? OR CAST(id AS TEXT) = ?",
            (operate_user_id, operate_user_id),
        ).fetchone()
    except Exception:
        row = None
    if row:
        return row["username"]
    try:
        row = db.execute(
            f'SELECT display_name FROM "{KB_SHARED_USER_SCHEMA}".users WHERE CAST(id AS TEXT) = ?',
            (operate_user_id,),
        ).fetchone()
    except Exception:
        row = None
    if row:
        return row["display_name"]
    return str(operate_user_id)


def _normalize_operate_type(value: str | None) -> str:
    if not value:
        return "SYSTEM_PERMISSION"
    return value if value in OPERATE_TYPES else "SYSTEM_PERMISSION"


def _format_detail(detail_json: dict[str, Any], raw: str | None) -> str:
    if detail_json:
        parts = []
        for key, value in detail_json.items():
            if isinstance(value, (list, dict)):
                parts.append(f"{key}={dumps(value)}")
            else:
                parts.append(f"{key}={value}")
        return "; ".join(parts)
    return raw or "{}"
