from datetime import UTC, datetime, timedelta

from flask import Blueprint, current_app, g, request
from sqlalchemy import func, or_, select, text

from ..extensions import db
from ..agents.team_service import AgentTeamService, normalize_member_codes
from ..models import Agent, AgentTeam, ModelConfig, Skill, Task, Tool
from ..services.agent_readiness import agent_readiness
from ..services.task_model_runtime import falls_back_to_personal_model, select_personal_chat_config
from .auth import serialize_user
from .responses import error, ok
from .tasks import resolve_agent_service, serialize_task


bp = Blueprint("workspace", __name__)

TASK_TYPE_AGENT_CODES = {
    "LITERATURE_SEARCH": "literature_search",
    "MANUSCRIPT_ASSISTANCE": "manuscript_assistance",
    "INNOVATION_POINT_GENERATION": "innovation_point_generation",
    "PAPER_READING": "paper_reading",
    "ACADEMIC_COMPLIANCE": "academic_compliance",
    "ACADEMIC_TRANSLATION": "academic_translation",
    "REVIEWER_COMMENTS": "reviewer_comments",
    "CONTRIBUTION_RECOMMENDATION": "contribution_recommendation",
    "PATENT_DRAFTING": "patent_drafting",
    "ACADEMIC_FIGURE": "academic_figure",
    "ARXIV_DAILY": "arxiv_daily",
}

TEAM_TEMPLATES = [
    {
        "id": "research-survey",
        "name": "领域调研智囊团",
        "description": "从跨源检索到研究空白识别，再形成可继续修改的调研文稿。",
        "members": ["literature_search", "innovation_point_generation", "manuscript_assistance"],
        "accent": "研究洞察",
    },
    {
        "id": "paper-submission",
        "name": "论文投稿智囊团",
        "description": "整理稿件论述、匹配投稿目标，并预演审稿问题与回复策略。",
        "members": ["manuscript_assistance", "contribution_recommendation", "reviewer_comments"],
        "accent": "论文投稿",
    },
    {
        "id": "technology-outcome",
        "name": "技术成果智囊团",
        "description": "检索相关工作、挖掘创新点并形成技术成果文稿，专利与绘图可按需接入。",
        "members": ["literature_search", "innovation_point_generation", "manuscript_assistance"],
        "accent": "成果转化",
    },
]


@bp.get("/workspace/summary")
def workspace_summary():
    week_start = datetime.now(UTC) - timedelta(days=7)
    user_id = g.current_user.id
    return ok(
        {
            "statistics": [
                {
                    "label": "知识库",
                    "value": db.session.scalar(
                        text(
                            "SELECT count(*) FROM zhiyan.knowledge_items "
                            "WHERE owner_user_id = :user_id AND deleted_at IS NULL"
                        ),
                        {"user_id": user_id},
                    )
                    or 0,
                    "unit": "个",
                },
                {
                    "label": "本周任务",
                    "value": db.session.scalar(
                        select(func.count(Task.id)).where(
                            Task.user_id == user_id,
                            Task.created_at >= week_start,
                        )
                    )
                    or 0,
                    "unit": "次",
                },
                {
                    "label": "收藏文献",
                    "value": db.session.scalar(
                        text("SELECT count(*) FROM zhiyan.paper_favorites WHERE user_id = :user_id"),
                        {"user_id": user_id},
                    )
                    or 0,
                    "unit": "篇",
                },
            ],
            "suggestions": [
                "检索近三年多智能体科研助手相关论文",
                "检索动态 RAG 的代表性方法与综述",
                "梳理图神经网络在分子关系学习中的研究脉络",
            ],
        }
    )


@bp.get("/agents")
def agents():
    items = db.session.scalars(select(Agent).where(Agent.status == "ACTIVE").order_by(Agent.name)).all()
    has_personal_model = bool(
        db.session.scalar(
            select(func.count(ModelConfig.id)).where(
                ModelConfig.owner_user_id == g.current_user.id,
                ModelConfig.config_scope == "USER",
                ModelConfig.status == "ACTIVE",
                ModelConfig.deleted_at.is_(None),
            )
        )
    )
    data = [
        {
            "id": str(item.id),
            "code": item.code,
            "name": item.name,
            "category": item.category,
            "description": item.description or "",
            "version": item.version,
            **agent_readiness(
                current_app,
                item.code,
                has_personal_model=has_personal_model,
            ),
            "route": (item.config_json or {}).get("route"),
            "capabilities": (item.config_json or {}).get("capabilities", []),
        }
        for item in items
    ]
    return ok(data, meta={"total": len(data)})


@bp.get("/agent-teams")
def agent_teams():
    items = db.session.scalars(
        select(AgentTeam)
        .where(
            AgentTeam.status == "ACTIVE",
            or_(AgentTeam.visibility == "PUBLIC", AgentTeam.owner_user_id == g.current_user.id),
        )
        .order_by(AgentTeam.updated_at.desc())
    ).all()
    agents_by_code = active_agents_by_code()
    data = [serialize_agent_team(item, agents_by_code) for item in items]
    return ok(data, meta={"total": len(data)})


@bp.get("/agent-team-templates")
def agent_team_templates():
    agents_by_code = active_agents_by_code()
    return ok(
        [
            {
                **template,
                "members": [serialize_team_member(code, agents_by_code) for code in template["members"]],
            }
            for template in TEAM_TEMPLATES
        ]
    )


@bp.post("/agent-teams")
def create_agent_team():
    payload = request.get_json(silent=True) or {}
    try:
        values = normalize_agent_team_payload(payload)
    except ValueError as exc:
        return error(str(exc), code="AGENT_TEAM_INVALID")
    item = AgentTeam(owner_user_id=g.current_user.id, visibility="PRIVATE", status="ACTIVE", **values)
    db.session.add(item)
    db.session.commit()
    return ok(serialize_agent_team(item, active_agents_by_code()), status=201)


@bp.get("/agent-teams/<uuid:team_id>")
def agent_team_detail(team_id):
    item = accessible_agent_team(team_id)
    if item is None:
        return error("智囊团不存在", code="AGENT_TEAM_NOT_FOUND", status=404)
    return ok(serialize_agent_team(item, active_agents_by_code()))


@bp.patch("/agent-teams/<uuid:team_id>")
def update_agent_team(team_id):
    item = owned_agent_team(team_id)
    if item is None:
        return error("只能编辑自己创建的智囊团", code="AGENT_TEAM_NOT_EDITABLE", status=404)
    payload = request.get_json(silent=True) or {}
    try:
        values = normalize_agent_team_payload(payload, existing=item)
    except ValueError as exc:
        return error(str(exc), code="AGENT_TEAM_INVALID")
    item.name = values["name"]
    item.description = values["description"]
    item.team_config = values["team_config"]
    item.version += 1
    db.session.commit()
    return ok(serialize_agent_team(item, active_agents_by_code()))


@bp.delete("/agent-teams/<uuid:team_id>")
def delete_agent_team(team_id):
    item = owned_agent_team(team_id)
    if item is None:
        return error("只能删除自己创建的智囊团", code="AGENT_TEAM_NOT_EDITABLE", status=404)
    item.status = "DISABLED"
    db.session.commit()
    return ok({"id": str(item.id), "deleted": True})


@bp.post("/agent-teams/<uuid:team_id>/runs")
def run_agent_team(team_id):
    item = accessible_agent_team(team_id)
    if item is None:
        return error("智囊团不存在", code="AGENT_TEAM_NOT_FOUND", status=404)
    payload = request.get_json(silent=True) or {}
    prompt = str(payload.get("prompt") or "").strip()
    if len(prompt) < 10:
        return error("请至少输入 10 个字符的科研目标", code="AGENT_TEAM_PROMPT_REQUIRED")
    if not normalize_member_codes((item.team_config or {}).get("members")):
        return error("智囊团尚未配置成员", code="AGENT_TEAM_EMPTY", status=409)
    # Every member inherits the parent's model, so resolve it once here.
    model_config = None
    requested_model = str(payload.get("model") or "auto")
    if requested_model.startswith("model_config:"):
        model_config = db.session.scalar(
            select(ModelConfig).where(
                ModelConfig.id == _optional_uuid(requested_model.split(":", 1)[1]),
                ModelConfig.owner_user_id == g.current_user.id,
                ModelConfig.config_scope == "USER",
                ModelConfig.status == "ACTIVE",
                ModelConfig.deleted_at.is_(None),
            )
        )
        if model_config is None:
            return error("所选个人模型不存在、未验证或已停用", code="MODEL_CONFIG_NOT_AVAILABLE", status=409)
    elif falls_back_to_personal_model(requested_model):
        model_config = select_personal_chat_config(g.current_user.id)
    task = Task(
        user_id=g.current_user.id,
        agent_team_id=item.id,
        model_config_id=model_config.id if model_config else None,
        task_type="AGENT_TEAM",
        status="QUEUED",
        progress=0,
        current_step="等待智囊团协调器启动",
        input_json={
            "prompt": prompt,
            "model": model_config.name if model_config else requested_model,
            "agent_code": "agent_team",
            "team_name": item.name,
        },
        output_json={"team": {"id": str(item.id), "name": item.name}, "stages": []},
        trace_summary={},
    )
    db.session.add(task)
    db.session.commit()
    service = current_app.extensions.get("agent_team_service")
    if service is None:
        service = AgentTeamService(current_app._get_current_object(), resolve_agent_service)
        current_app.extensions["agent_team_service"] = service
    service.start(task.id, g.current_user.id)
    return ok(serialize_task(task), status=201)


@bp.get("/agent-team-runs/<uuid:task_id>")
def agent_team_run(task_id):
    task = db.session.get(Task, task_id)
    if task is None or task.task_type != "AGENT_TEAM" or (
        task.user_id != g.current_user.id and g.current_user.role_code != "system_admin"
    ):
        return error("智囊团任务不存在", code="AGENT_TEAM_RUN_NOT_FOUND", status=404)
    return ok(serialize_task(task))


def active_agents_by_code() -> dict[str, Agent]:
    return {item.code: item for item in db.session.scalars(select(Agent).where(Agent.status == "ACTIVE")).all()}


def serialize_team_member(code: str, agents_by_code: dict[str, Agent]) -> dict:
    agent = agents_by_code.get(code)
    config = agent.config_json if agent else {}
    return {
        "code": code,
        "name": agent.name if agent else code,
        "category": agent.category if agent else "未知",
        "route": (config or {}).get("route"),
        "available": agent is not None,
        "requires_input": code not in {"literature_search", "manuscript_assistance", "innovation_point_generation", "reviewer_comments", "contribution_recommendation"},
    }


def serialize_agent_team(item: AgentTeam, agents_by_code: dict[str, Agent]) -> dict:
    config = item.team_config or {}
    codes = normalize_member_codes(config.get("members"))
    return {
        "id": str(item.id),
        "name": item.name,
        "description": item.description or "",
        "visibility": item.visibility,
        "version": item.version,
        "status": "可用" if item.status == "ACTIVE" else item.status,
        "mode": config.get("mode") or "sequential",
        "members": [serialize_team_member(code, agents_by_code) for code in codes],
        "member_names": [serialize_team_member(code, agents_by_code)["name"] for code in codes],
        "editable": item.owner_user_id == g.current_user.id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def normalize_agent_team_payload(payload: dict, existing: AgentTeam | None = None) -> dict:
    name = str(payload.get("name") if "name" in payload else (existing.name if existing else "")).strip()
    description = str(payload.get("description") if "description" in payload else (existing.description if existing else "")).strip()
    current = (existing.team_config or {}) if existing else {}
    raw_members = payload.get("members", current.get("members"))
    members = normalize_member_codes(raw_members)
    if not name or len(name) > 80:
        raise ValueError("智囊团名称需为 1 至 80 个字符")
    if len(description) > 500:
        raise ValueError("智囊团说明不能超过 500 个字符")
    if not 2 <= len(members) <= 8:
        raise ValueError("请选择 2 至 8 个 Agent")
    available = active_agents_by_code()
    missing = [code for code in members if code not in available]
    if missing:
        raise ValueError(f"以下 Agent 未启用：{', '.join(missing)}")
    return {
        "name": name,
        "description": description,
        "team_config": {"mode": "sequential", "members": members},
    }


def accessible_agent_team(team_id) -> AgentTeam | None:
    return db.session.scalar(
        select(AgentTeam).where(
            AgentTeam.id == team_id,
            AgentTeam.status == "ACTIVE",
            or_(AgentTeam.visibility == "PUBLIC", AgentTeam.owner_user_id == g.current_user.id),
        )
    )


def owned_agent_team(team_id) -> AgentTeam | None:
    return db.session.scalar(
        select(AgentTeam).where(
            AgentTeam.id == team_id,
            AgentTeam.owner_user_id == g.current_user.id,
            AgentTeam.status == "ACTIVE",
        )
    )


@bp.get("/tools")
def tools():
    items = db.session.scalars(select(Tool).where(Tool.status == "ACTIVE").order_by(Tool.name)).all()
    data = [
        {
            "id": str(item.id),
            "code": item.code,
            "name": item.name,
            "category": item.category,
            "description": item.description or "",
            "status": "可用",
            "route": (item.config_json or {}).get("route"),
            "capabilities": (item.config_json or {}).get("capabilities", []),
        }
        for item in items
    ]
    return ok(data, meta={"total": len(data)})


@bp.get("/skills")
def skills():
    items = db.session.scalars(
        select(Skill)
        .where(
            Skill.deleted_at.is_(None),
            or_(Skill.visibility == "PUBLIC", Skill.owner_user_id == g.current_user.id),
        )
        .order_by(Skill.updated_at.desc())
    ).all()
    data = [
        {
            "id": str(item.id),
            "name": item.name,
            "description": item.description or "",
            "status": item.review_status,
            "category": ((item.definition_json or {}).get("source") or {}).get("category") or "科研技能",
            "tags": ((item.definition_json or {}).get("source") or {}).get("tags") or [],
            "route": f"/skills/{item.id}",
        }
        for item in items
    ]
    return ok(data, meta={"total": len(data)})


@bp.get("/skills/<uuid:skill_id>")
def skill_detail(skill_id):
    item = db.session.scalar(
        select(Skill).where(
            Skill.id == skill_id,
            Skill.deleted_at.is_(None),
            or_(Skill.visibility == "PUBLIC", Skill.owner_user_id == g.current_user.id),
        )
    )
    if item is None:
        return ok(None, meta={"found": False})
    definition = item.definition_json or {}
    source = definition.get("source") or {}
    download = definition.get("download") or {}
    return ok(
        {
            "id": str(item.id),
            "name": item.name,
            "description": item.description or "",
            "status": item.review_status,
            "category": source.get("category") or "科研技能",
            "tags": source.get("tags") or [],
            "author": source.get("author") or "",
            "sourceSite": source.get("site") or "",
            "sourceUrl": definition.get("source_url") or "",
            "installUrl": definition.get("install_url") or "",
            "downloadStatus": download.get("status") or "METADATA_ONLY",
            "downloadedAt": download.get("downloaded_at"),
            "fileCount": download.get("file_count") or len(download.get("files") or {}),
            "fullContent": download.get("full_content") or item.description or "",
            "files": [
                {"path": path, "content": content}
                for path, content in (download.get("files") or {}).items()
            ],
            "downloadError": download.get("error") or "",
        }
    )


@bp.get("/knowledge-bases")
def knowledge_bases():
    rows = db.session.execute(
        text(
            """
            SELECT id, title AS name, metadata, updated_at
            FROM zhiyan.knowledge_items
            WHERE owner_user_id = :user_id AND deleted_at IS NULL
            ORDER BY updated_at DESC
            LIMIT 100
            """
        ),
        {"user_id": g.current_user.id},
    ).mappings().all()
    data = [
        {
            "id": str(row["id"]),
            "name": row["name"],
            "documents": 1,
            "datasets": 0,
            "tags": (row["metadata"] or {}).get("tags", []),
            "updatedAt": row["updated_at"].isoformat(),
        }
        for row in rows
    ]
    return ok(data, meta={"total": len(data)})


@bp.get("/history")
def history():
    tasks = db.session.scalars(
        select(Task)
        .where(Task.user_id == g.current_user.id)
        .order_by(Task.created_at.desc())
        .limit(12)
    ).all()
    return ok(
        [
            {
                "id": str(task.id),
                "title": str((task.input_json or {}).get("prompt") or "未命名任务")[:32],
                "time": task.created_at.astimezone().strftime("%m-%d %H:%M"),
                "agentCode": str(
                    (task.input_json or {}).get("agent_code")
                    or TASK_TYPE_AGENT_CODES.get(task.task_type, "")
                ),
            }
            for task in tasks
        ],
        meta={"total": len(tasks)},
    )


@bp.get("/users/me")
def current_user():
    user = g.current_user
    profile = serialize_user(user)
    return ok(
        {
            **profile,
            "modelConfigured": db.session.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM zhiyan.model_configs WHERE owner_user_id = :user_id AND status = 'ACTIVE')"
                ),
                {"user_id": user.id},
            ),
        }
    )


def _optional_uuid(value: str):
    from uuid import UUID

    try:
        return UUID(str(value))
    except ValueError:
        return UUID(int=0)
