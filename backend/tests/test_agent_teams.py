from types import SimpleNamespace
from uuid import uuid4

import pytest
from flask import g

from app import create_app
from app.agents.team_service import normalize_member_codes, summarize_output
from app.api import workspace as workspace_api
from app.extensions import db


def test_team_member_codes_are_ordered_and_unique():
    assert normalize_member_codes(
        [{"code": "literature_search"}, "innovation_point_generation", "literature_search", {}]
    ) == ["literature_search", "innovation_point_generation"]


def test_team_output_summary_keeps_handoff_fields_and_limits_large_lists():
    output = {
        "papers": [{"title": f"paper-{index}"} for index in range(20)],
        "research_gaps": ["gap"],
        "internal_path": "E:/private/output.json",
    }
    summary = summarize_output(output)
    assert len(summary["papers"]) == 8
    assert summary["research_gaps"] == ["gap"]
    assert "internal_path" not in summary


def test_team_payload_requires_two_active_agents(monkeypatch):
    active = {
        "literature_search": SimpleNamespace(code="literature_search"),
        "manuscript_assistance": SimpleNamespace(code="manuscript_assistance"),
    }
    monkeypatch.setattr(workspace_api, "active_agents_by_code", lambda: active)
    values = workspace_api.normalize_agent_team_payload(
        {
            "name": "调研团队",
            "description": "检索后形成文稿",
            "members": ["literature_search", "manuscript_assistance"],
        }
    )
    assert values["team_config"]["members"] == ["literature_search", "manuscript_assistance"]
    assert values["team_config"]["mode"] == "sequential"

    with pytest.raises(ValueError, match="2 至 8"):
        workspace_api.normalize_agent_team_payload(
            {"name": "单成员", "members": ["literature_search"]}
        )


def test_team_run_rejects_short_goal(monkeypatch):
    app = create_app({"TESTING": True, "KNOWLEDGE_BASE_EMBEDDED": False})
    team = SimpleNamespace(id=uuid4(), team_config={"members": ["a", "b"]})
    monkeypatch.setattr(workspace_api, "accessible_agent_team", lambda _team_id: team)
    with app.test_request_context(json={"prompt": "太短"}):
        g.current_user = SimpleNamespace(id=uuid4(), role_code="normal_user")
        response, status = workspace_api.run_agent_team(team.id)
    assert status == 400
    assert response.get_json()["error"]["code"] == "AGENT_TEAM_PROMPT_REQUIRED"


def test_private_team_lookup_is_owner_scoped(monkeypatch):
    app = create_app({"TESTING": True, "KNOWLEDGE_BASE_EMBEDDED": False})
    monkeypatch.setattr(db.session, "scalar", lambda _query: None)
    with app.test_request_context():
        g.current_user = SimpleNamespace(id=uuid4(), role_code="normal_user")
        assert workspace_api.accessible_agent_team(uuid4()) is None
