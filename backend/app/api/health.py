from flask import Blueprint, current_app
from sqlalchemy import text

from ..extensions import db
from ..services.agent_readiness import agent_readiness
from .responses import ok

bp = Blueprint("health", __name__)


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

    agents = {
        code: agent_readiness(current_app, code)
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
            "components": {"api": "up", "database": database},
            "agents": agents,
        }
    )
