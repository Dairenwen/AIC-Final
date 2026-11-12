from flask import Blueprint, current_app
from sqlalchemy import select, text

from ..extensions import db
from ..models import ModelConfig
from ..services import shared_cache
from ..services.agent_readiness import agent_readiness, endpoint_reachable
from ..services.model_credentials import decrypt_api_key
from .responses import ok

bp = Blueprint("health", __name__)


def verified_default_api_available() -> bool:
    """Check existing verified account defaults without invoking a model API."""
    try:
        items = db.session.scalars(select(ModelConfig).where(
            ModelConfig.config_scope == "USER",
            ModelConfig.model_type_code == "chat",
            ModelConfig.status == "ACTIVE",
            ModelConfig.deleted_at.is_(None),
            ModelConfig.default_for.contains(["chat"]),
            ModelConfig.last_verified_at.is_not(None),
            ModelConfig.encrypted_api_key.is_not(None),
            ModelConfig.key_nonce.is_not(None),
            ModelConfig.key_version.is_not(None),
        )).all()
        credentials = [
            (item.encrypted_api_key, item.key_nonce, item.key_version, item.base_url)
            for item in items
        ]
    except Exception:
        # A missing or unrestored model table cannot make API-only readiness pass.
        return False
    finally:
        # Health reads are read-only; release the connection before network probes.
        db.session.rollback()
    timeout = float(current_app.config["AGENT_READINESS_CONNECT_TIMEOUT_SECONDS"])
    for encrypted, nonce, version, base_url in credentials:
        try:
            key = decrypt_api_key(encrypted, nonce, version)
        except (RuntimeError, ValueError, TypeError, UnicodeError):
            continue
        if key.strip() and endpoint_reachable(base_url, timeout):
            return True
    return False


@bp.get("/live")
def live():
    return ok({"status": "up", "service": "api"})


@bp.get("/ready")
def ready():
    database = "up"
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:
        db.session.rollback()
        database = "unavailable"

    has_default_api = database == "up" and verified_default_api_available()
    agents = {
        code: agent_readiness(current_app, code, has_personal_model=has_default_api)
        for code in (
            "literature_search",
            "manuscript_assistance",
            "innovation_point_generation",
            "paper_reading",
            "patent_drafting",
            "academic_compliance",
            "arxiv_daily",
            "academic_figure",
            "academic_translation",
            "reviewer_comments",
            "contribution_recommendation",
        )
    }
    overall_status = (
        "ready"
        if database == "up" and all(item["readiness"] == "READY" for item in agents.values())
        else "degraded"
    )

    return ok(
        {
            "status": overall_status,
            # Redis is optional: "not_configured" and "unavailable" both mean
            # the service is running on in-process state, not that it is down.
            "components": {"api": "up", "database": database, "redis": shared_cache.status()},
            "agents": agents,
        }
    )
