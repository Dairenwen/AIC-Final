from __future__ import annotations

from flask import Blueprint, g

from ..services.token_usage import usage_snapshot
from .responses import ok


bp = Blueprint("usage", __name__)


@bp.get("/model-usage")
def model_usage():
    return ok(usage_snapshot(g.current_user.id, g.current_user.role_code))

