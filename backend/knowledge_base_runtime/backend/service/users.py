from __future__ import annotations

import secrets
from typing import Any

from knowledge_base_runtime.backend.dao.database import get_db, utc_now
from knowledge_base_runtime.backend.service.audit import record_audit_log


VALID_ROLES = {"普通用户", "数据管理员", "系统管理员"}


def list_users(page: int = 1, size: int = 50) -> dict[str, Any]:
    page = max(page, 1)
    size = min(max(size, 1), 100)
    offset = (page - 1) * size
    with get_db() as db:
        total = db.execute("SELECT COUNT(*) AS c FROM service_accounts").fetchone()["c"]
        rows = db.execute(
            "SELECT * FROM service_accounts ORDER BY created_at, id LIMIT ? OFFSET ?",
            (size, offset),
        ).fetchall()
    return {"total": total, "page": page, "size": size, "list": [_serialize(row) for row in rows]}


def create_user(
    payload: dict[str, Any],
    operate_user_id: str = "system",
    ip: str | None = None,
) -> dict[str, Any]:
    username = str(payload.get("username") or "").strip()
    if not username:
        raise ValueError("username is required")
    role = _validate_role(payload.get("role"))
    with get_db() as db:
        if db.execute("SELECT id FROM service_accounts WHERE username = ?", (username,)).fetchone():
            raise ValueError("username already exists")
        db.execute(
            """
            INSERT INTO service_accounts(username, role, api_key, call_count, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (username, role, _new_api_key(username), 0, True, utc_now(), utc_now()),
        )
        row = db.execute("SELECT * FROM service_accounts WHERE username = ?", (username,)).fetchone()
        record_audit_log(
            db,
            operate_user_id=operate_user_id,
            user_ip=ip,
            operate_type="SYSTEM_PERMISSION",
            operate_sub_type="ACCOUNT_CREATE",
            target_resource_type="user",
            target_resource_id=str(row["id"]),
            resource_title=username,
            operate_content={"username": username, "role": role},
            is_system_op=operate_user_id == "system",
        )
    return _serialize(row)


def update_user_role(
    user_id: int,
    role: Any,
    operate_user_id: str = "system",
    ip: str | None = None,
) -> dict[str, Any]:
    normalized = _validate_role(role)
    return _update_user(user_id, "role", normalized, operate_user_id=operate_user_id, ip=ip)


def update_user_status(
    user_id: int,
    active: Any,
    operate_user_id: str = "system",
    ip: str | None = None,
) -> dict[str, Any]:
    return _update_user(user_id, "active", bool(active), operate_user_id=operate_user_id, ip=ip)


def reset_api_key(user_id: int, operate_user_id: str = "system", ip: str | None = None) -> dict[str, Any]:
    with get_db() as db:
        row = db.execute("SELECT username FROM service_accounts WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise ValueError("user not found")
        api_key = _new_api_key(row["username"])
        db.execute("UPDATE service_accounts SET api_key = ?, updated_at = ? WHERE id = ?", (api_key, utc_now(), user_id))
        record_audit_log(
            db,
            operate_user_id=operate_user_id,
            user_ip=ip,
            operate_type="SYSTEM_PERMISSION",
            operate_sub_type="API_KEY_RESET",
            target_resource_type="user",
            target_resource_id=str(user_id),
            resource_title=row["username"],
            operate_content={"username": row["username"], "api_key_reset": True},
            is_system_op=operate_user_id == "system",
        )
    return {"api_key": api_key}


def delete_user(user_id: int, operate_user_id: str = "system", ip: str | None = None) -> dict[str, Any]:
    with get_db() as db:
        row = db.execute("SELECT username, role FROM service_accounts WHERE id = ?", (user_id,)).fetchone()
        cur = db.execute("DELETE FROM service_accounts WHERE id = ?", (user_id,))
        record_audit_log(
            db,
            operate_user_id=operate_user_id,
            user_ip=ip,
            operate_type="SYSTEM_PERMISSION",
            operate_sub_type="ACCOUNT_DELETE",
            target_resource_type="user",
            target_resource_id=str(user_id),
            resource_title=row["username"] if row else str(user_id),
            operate_content={"deleted": cur.rowcount, "before": dict(row) if row else None},
            is_system_op=operate_user_id == "system",
        )
    return {"deleted": cur.rowcount}


def _update_user(
    user_id: int,
    field: str,
    value: Any,
    *,
    operate_user_id: str,
    ip: str | None,
) -> dict[str, Any]:
    if field not in {"role", "active"}:
        raise ValueError("unsupported user field")
    with get_db() as db:
        before = db.execute("SELECT * FROM service_accounts WHERE id = ?", (user_id,)).fetchone()
        cur = db.execute(f"UPDATE service_accounts SET {field} = ?, updated_at = ? WHERE id = ?", (value, utc_now(), user_id))
        if cur.rowcount == 0:
            raise ValueError("user not found")
        row = db.execute("SELECT * FROM service_accounts WHERE id = ?", (user_id,)).fetchone()
        record_audit_log(
            db,
            operate_user_id=operate_user_id,
            user_ip=ip,
            operate_type="SYSTEM_PERMISSION",
            operate_sub_type="ROLE_CHANGE" if field == "role" else "ACCOUNT_STATUS_CHANGE",
            target_resource_type="user",
            target_resource_id=str(user_id),
            resource_title=row["username"],
            operate_content={
                "field": field,
                "before": dict(before).get(field) if before else None,
                "after": value,
            },
            is_system_op=operate_user_id == "system",
        )
    return _serialize(row)


def _validate_role(role: Any) -> str:
    normalized = str(role or "普通用户")
    if normalized not in VALID_ROLES:
        raise ValueError("invalid role")
    return normalized


def _new_api_key(username: str) -> str:
    return f"sk-{username}-{secrets.token_urlsafe(12)}"


def _serialize(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["active"] = bool(item.get("active"))
    return item
