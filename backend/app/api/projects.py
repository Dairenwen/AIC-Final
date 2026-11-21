from __future__ import annotations

import json
from uuid import UUID

from flask import Blueprint, g, request
from sqlalchemy import func, or_, select

from ..extensions import db
from ..models import (
    Artifact,
    Conversation,
    DocumentVersion,
    Message,
    Project,
    ProjectDocument,
    ProjectMember,
    Task,
)
from .responses import error, ok


bp = Blueprint("projects", __name__)
EDIT_ROLES = {"OWNER", "EDITOR"}


def get_project_access(project_id: UUID, *, edit: bool = False) -> tuple[Project, str] | None:
    project = db.session.get(Project, project_id)
    if project is None or project.status == "DELETED":
        return None
    if g.current_user.role_code == "system_admin":
        return project, "OWNER"
    membership = db.session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == g.current_user.id,
        )
    )
    if membership is None or (edit and membership.role not in EDIT_ROLES):
        return None
    return project, membership.role


def get_conversation_access(conversation_id: UUID, *, edit: bool = False) -> tuple[Conversation, Project, str] | None:
    conversation = db.session.get(Conversation, conversation_id)
    if conversation is None or conversation.status == "DELETED":
        return None
    access = get_project_access(conversation.project_id, edit=edit)
    if access is None:
        return None
    return conversation, access[0], access[1]


@bp.get("/projects")
def list_projects():
    if g.current_user.role_code == "system_admin":
        query = select(Project).where(Project.status != "DELETED")
    else:
        query = (
            select(Project, ProjectMember.role)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(ProjectMember.user_id == g.current_user.id, Project.status != "DELETED")
        )
    if g.current_user.role_code == "system_admin":
        projects = db.session.scalars(query.order_by(Project.updated_at.desc())).all()
        data = [serialize_project(item, role="OWNER") for item in projects]
    else:
        rows = db.session.execute(query.order_by(Project.updated_at.desc())).all()
        data = [serialize_project(project, role=role) for project, role in rows]
    return ok(data, meta={"total": len(data)})


@bp.post("/projects")
def create_project():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    if not name:
        return error("请输入项目名称", code="PROJECT_NAME_REQUIRED")
    project = Project(
        owner_user_id=g.current_user.id,
        name=name[:160],
        description=str(payload.get("description") or "").strip() or None,
        research_goal=str(payload.get("research_goal") or "").strip() or None,
        settings_json=payload.get("settings") if isinstance(payload.get("settings"), dict) else {},
    )
    db.session.add(project)
    db.session.flush()
    db.session.add(ProjectMember(project_id=project.id, user_id=g.current_user.id, role="OWNER"))
    db.session.commit()
    return ok(serialize_project(project, role="OWNER"), status=201)


@bp.get("/projects/<uuid:project_id>")
def get_project(project_id: UUID):
    access = get_project_access(project_id)
    if access is None:
        return project_not_found()
    return ok(serialize_project(access[0], role=access[1]))


@bp.patch("/projects/<uuid:project_id>")
def update_project(project_id: UUID):
    access = get_project_access(project_id, edit=True)
    if access is None:
        return project_not_found()
    project, role = access
    payload = request.get_json(silent=True) or {}
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            return error("项目名称不能为空", code="PROJECT_NAME_REQUIRED")
        project.name = name[:160]
    if "description" in payload:
        project.description = str(payload.get("description") or "").strip() or None
    if "research_goal" in payload:
        project.research_goal = str(payload.get("research_goal") or "").strip() or None
    if "settings" in payload and isinstance(payload["settings"], dict):
        project.settings_json = payload["settings"]
    db.session.commit()
    return ok(serialize_project(project, role=role))


@bp.get("/projects/<uuid:project_id>/workspace")
def project_workspace(project_id: UUID):
    access = get_project_access(project_id)
    if access is None:
        return project_not_found()
    project, role = access
    documents = db.session.scalars(
        select(ProjectDocument)
        .where(ProjectDocument.project_id == project_id, ProjectDocument.status == "ACTIVE")
        .order_by(ProjectDocument.updated_at.desc())
    ).all()
    conversations = db.session.scalars(
        select(Conversation)
        .where(Conversation.project_id == project_id, Conversation.status == "ACTIVE")
        .order_by(Conversation.updated_at.desc())
    ).all()
    tasks = db.session.scalars(
        select(Task).where(Task.project_id == project_id).order_by(Task.created_at.desc()).limit(12)
    ).all()
    artifacts = db.session.scalars(
        select(Artifact)
        .where(Artifact.project_id == project_id, Artifact.status == "ACTIVE")
        .order_by(Artifact.updated_at.desc())
        .limit(20)
    ).all()
    return ok(
        {
            "project": serialize_project(project, role=role),
            "documents": [serialize_document(item, include_content=False) for item in documents],
            "conversations": [serialize_conversation(item) for item in conversations],
            "tasks": [serialize_workspace_task(item) for item in tasks],
            "artifacts": [serialize_artifact(item) for item in artifacts],
        }
    )


@bp.get("/projects/<uuid:project_id>/documents")
def list_documents(project_id: UUID):
    if get_project_access(project_id) is None:
        return project_not_found()
    items = db.session.scalars(
        select(ProjectDocument)
        .where(ProjectDocument.project_id == project_id, ProjectDocument.status == "ACTIVE")
        .order_by(ProjectDocument.updated_at.desc())
    ).all()
    return ok([serialize_document(item, include_content=False) for item in items])


@bp.post("/projects/<uuid:project_id>/documents")
def create_document(project_id: UUID):
    if get_project_access(project_id, edit=True) is None:
        return project_not_found()
    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title") or "").strip()
    if not title:
        return error("请输入文档标题", code="DOCUMENT_TITLE_REQUIRED")
    content = str(payload.get("content") or "")
    document = ProjectDocument(
        project_id=project_id,
        owner_user_id=g.current_user.id,
        title=title[:200],
        document_type=str(payload.get("document_type") or "MARKDOWN")[:40],
        content=content,
        content_json=payload.get("content_json") if isinstance(payload.get("content_json"), dict) else {},
        current_version=1,
    )
    db.session.add(document)
    db.session.flush()
    db.session.add(
        DocumentVersion(
            document_id=document.id,
            version_no=1,
            content=content,
            content_json=document.content_json,
            created_by=g.current_user.id,
        )
    )
    db.session.commit()
    return ok(serialize_document(document), status=201)


@bp.get("/projects/<uuid:project_id>/documents/<uuid:document_id>")
def get_document(project_id: UUID, document_id: UUID):
    if get_project_access(project_id) is None:
        return project_not_found()
    document = find_project_document(project_id, document_id)
    if document is None:
        return error("文档不存在", code="DOCUMENT_NOT_FOUND", status=404)
    return ok(serialize_document(document))


@bp.patch("/projects/<uuid:project_id>/documents/<uuid:document_id>")
def update_document(project_id: UUID, document_id: UUID):
    if get_project_access(project_id, edit=True) is None:
        return project_not_found()
    document = find_project_document(project_id, document_id)
    if document is None:
        return error("文档不存在", code="DOCUMENT_NOT_FOUND", status=404)
    payload = request.get_json(silent=True) or {}
    expected_version = payload.get("version")
    if not isinstance(expected_version, int):
        return error("保存文档时必须提供当前版本号", code="DOCUMENT_VERSION_REQUIRED")
    if expected_version != document.current_version:
        return error("文档已被其他成员更新，请刷新后重试", code="DOCUMENT_VERSION_CONFLICT", status=409)
    if "title" in payload:
        title = str(payload.get("title") or "").strip()
        if not title:
            return error("文档标题不能为空", code="DOCUMENT_TITLE_REQUIRED")
        document.title = title[:200]
    if "content" in payload:
        document.content = str(payload.get("content") or "")
    if "content_json" in payload and isinstance(payload["content_json"], dict):
        document.content_json = payload["content_json"]
    document.current_version += 1
    db.session.add(
        DocumentVersion(
            document_id=document.id,
            version_no=document.current_version,
            content=document.content,
            content_json=document.content_json,
            created_by=g.current_user.id,
        )
    )
    db.session.commit()
    return ok(serialize_document(document))


@bp.get("/projects/<uuid:project_id>/conversations")
def list_conversations(project_id: UUID):
    if get_project_access(project_id) is None:
        return project_not_found()
    items = db.session.scalars(
        select(Conversation)
        .where(Conversation.project_id == project_id, Conversation.status == "ACTIVE")
        .order_by(Conversation.updated_at.desc())
    ).all()
    return ok([serialize_conversation(item) for item in items])


@bp.post("/projects/<uuid:project_id>/conversations")
def create_conversation(project_id: UUID):
    if get_project_access(project_id, edit=True) is None:
        return project_not_found()
    payload = request.get_json(silent=True) or {}
    conversation = Conversation(
        project_id=project_id,
        user_id=g.current_user.id,
        title=(str(payload.get("title") or "新对话").strip() or "新对话")[:200],
        context_json=payload.get("context") if isinstance(payload.get("context"), dict) else {},
    )
    db.session.add(conversation)
    db.session.commit()
    return ok(serialize_conversation(conversation), status=201)


@bp.get("/conversations/<uuid:conversation_id>/messages")
def list_messages(conversation_id: UUID):
    if get_conversation_access(conversation_id) is None:
        return error("对话不存在", code="CONVERSATION_NOT_FOUND", status=404)
    items = db.session.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.sequence)
    ).all()
    return ok([serialize_message(item) for item in items])


@bp.post("/conversations/<uuid:conversation_id>/messages")
def create_message(conversation_id: UUID):
    access = get_conversation_access(conversation_id, edit=True)
    if access is None:
        return error("对话不存在", code="CONVERSATION_NOT_FOUND", status=404)
    payload = request.get_json(silent=True) or {}
    content = str(payload.get("content") or "").strip()
    role = str(payload.get("role") or "user").lower()
    if not content:
        return error("消息内容不能为空", code="MESSAGE_CONTENT_REQUIRED")
    if role not in {"user", "assistant", "system"}:
        return error("消息角色无效", code="MESSAGE_ROLE_INVALID")
    message = append_message(conversation_id, role, content, payload.get("content_json"))
    access[0].updated_at = func.now()
    db.session.commit()
    return ok(serialize_message(message), status=201)


@bp.post("/projects/<uuid:project_id>/artifacts/from-task")
def save_task_artifact(project_id: UUID):
    if get_project_access(project_id, edit=True) is None:
        return project_not_found()
    payload = request.get_json(silent=True) or {}
    try:
        task_id = UUID(str(payload.get("task_id") or ""))
    except ValueError:
        return error("请选择有效任务", code="TASK_ID_REQUIRED")
    task = db.session.get(Task, task_id)
    if task is None or (task.user_id != g.current_user.id and g.current_user.role_code != "system_admin"):
        return error("任务不存在", code="TASK_NOT_FOUND", status=404)
    if task.project_id and task.project_id != project_id:
        return error("任务已归属于其他项目", code="TASK_PROJECT_CONFLICT", status=409)
    task.project_id = project_id
    name = (str(payload.get("name") or task.input_json.get("prompt") or "Agent 研究产物").strip())[:240]
    artifact = Artifact(
        project_id=project_id,
        owner_user_id=g.current_user.id,
        task_id=task.id,
        artifact_type=str(payload.get("artifact_type") or task.task_type)[:60],
        name=name,
        object_key=str(payload.get("object_key") or "").strip() or None,
        content_json=task.output_json or {},
        metadata_json={"task_status": task.status, "agent_id": str(task.agent_id) if task.agent_id else None},
    )
    db.session.add(artifact)
    document = None
    if payload.get("create_document"):
        content = str(payload.get("content") or "").strip()
        if not content:
            content = json.dumps(task.output_json or {}, ensure_ascii=False, indent=2)
        document = ProjectDocument(
            project_id=project_id,
            owner_user_id=g.current_user.id,
            title=name[:200],
            document_type="MARKDOWN",
            content=content,
            content_json={"source_artifact_id": str(artifact.id)},
        )
        db.session.add(document)
        db.session.flush()
        db.session.add(
            DocumentVersion(
                document_id=document.id,
                version_no=1,
                content=content,
                content_json=document.content_json,
                source_task_id=task.id,
                created_by=g.current_user.id,
            )
        )
    db.session.commit()
    return ok(
        {"artifact": serialize_artifact(artifact), "document": serialize_document(document) if document else None},
        status=201,
    )


def append_message(conversation_id: UUID, role: str, content: str, content_json=None) -> Message:
    last_sequence = db.session.scalar(
        select(func.max(Message.sequence)).where(Message.conversation_id == conversation_id)
    ) or 0
    message = Message(
        conversation_id=conversation_id,
        role=role.upper(),
        content=content,
        content_json=content_json if isinstance(content_json, dict) else {},
        sequence=last_sequence + 1,
    )
    db.session.add(message)
    return message


def find_project_document(project_id: UUID, document_id: UUID) -> ProjectDocument | None:
    return db.session.scalar(
        select(ProjectDocument).where(
            ProjectDocument.id == document_id,
            ProjectDocument.project_id == project_id,
            ProjectDocument.status == "ACTIVE",
        )
    )


def serialize_project(project: Project, role: str | None = None) -> dict:
    return {
        "id": str(project.id),
        "name": project.name,
        "description": project.description or "",
        "research_goal": project.research_goal or "",
        "status": project.status,
        "role": role,
        "settings": project.settings_json or {},
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


def serialize_document(document: ProjectDocument, *, include_content: bool = True) -> dict:
    data = {
        "id": str(document.id),
        "project_id": str(document.project_id),
        "title": document.title,
        "document_type": document.document_type,
        "version": document.current_version,
        "status": document.status,
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
    }
    if include_content:
        data.update({"content": document.content, "content_json": document.content_json or {}})
    return data


def serialize_conversation(conversation: Conversation) -> dict:
    return {
        "id": str(conversation.id),
        "project_id": str(conversation.project_id),
        "title": conversation.title,
        "status": conversation.status,
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
    }


def serialize_message(message: Message) -> dict:
    return {
        "id": str(message.id),
        "conversation_id": str(message.conversation_id),
        "role": message.role.lower(),
        "content": message.content,
        "content_json": message.content_json or {},
        "sequence": message.sequence,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def serialize_workspace_task(task: Task) -> dict:
    return {
        "id": str(task.id),
        "title": str((task.input_json or {}).get("prompt") or "未命名任务")[:80],
        "agent_code": str((task.input_json or {}).get("agent_code") or ""),
        "status": task.status,
        "progress": task.progress,
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


def serialize_artifact(artifact: Artifact) -> dict:
    return {
        "id": str(artifact.id),
        "project_id": str(artifact.project_id),
        "task_id": str(artifact.task_id) if artifact.task_id else None,
        "artifact_type": artifact.artifact_type,
        "name": artifact.name,
        "version": artifact.version_no,
        "object_key": artifact.object_key,
        "metadata": artifact.metadata_json or {},
        "updated_at": artifact.updated_at.isoformat() if artifact.updated_at else None,
    }


def project_not_found():
    return error("项目不存在", code="PROJECT_NOT_FOUND", status=404)
