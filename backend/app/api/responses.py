from typing import Any

from flask import jsonify


def ok(data: Any = None, *, meta: dict | None = None, status: int = 200):
    payload: dict[str, Any] = {"success": True, "data": data}
    if meta is not None:
        payload["meta"] = meta
    return jsonify(payload), status


def error(message: str, *, code: str = "BAD_REQUEST", status: int = 400):
    return jsonify(
        {
            "success": False,
            "error": {"code": code, "message": message},
        }
    ), status

