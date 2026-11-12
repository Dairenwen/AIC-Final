from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import UUID

from flask import Blueprint, Response, current_app, g, request, send_file, stream_with_context
from sqlalchemy import select

from ..agents.Innovation_point_generation import InnovationPointGenerationService
from ..agents.academic_compliance import AcademicComplianceService
from ..agents.academic_figure import AcademicFigureService
from ..agents.academic_translation import AcademicTranslationService
from ..agents.academic_translation.service import (
    normalize_translation_options,
    normalize_translation_warning_items,
)
from ..agents.arxiv_daily import ArxivDailyService
from ..agents.arxiv_daily.service import normalize_arxiv_daily_options
from ..agents.contribution_recommendation import ContributionRecommendationService
from ..agents.literature_search import LiteratureSearchService
from ..agents.manuscript_assistance import ManuscriptAssistanceService
from ..agents.paper_reading import PaperReadingService
from ..agents.paper_reading.service import normalize_paper_reading_options
from ..agents.patent_drafting import PatentDraftingService
from ..agents.reviewer_comments import ReviewerCommentsService
from ..extensions import db
from ..models import Agent, ModelConfig, PatentDraftingRun, Task, TaskEvent, User
from ..services.agent_readiness import agent_readiness
from .projects import get_conversation_access, get_project_access
from .responses import error, ok
from .uploads import resolve_translation_upload


bp = Blueprint("tasks", __name__)
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELED"}
STREAM_END_STATES = {*TERMINAL_STATES, "WAITING_INPUT"}
AGENT_SERVICE_CLASSES = {
    "literature_search": LiteratureSearchService,
    "manuscript_assistance": ManuscriptAssistanceService,
    "innovation_point_generation": InnovationPointGenerationService,
    "academic_compliance": AcademicComplianceService,
    "academic_figure": AcademicFigureService,
    "arxiv_daily": ArxivDailyService,
    "academic_translation": AcademicTranslationService,
    "paper_reading": PaperReadingService,
    "patent_drafting": PatentDraftingService,
    "reviewer_comments": ReviewerCommentsService,
    "contribution_recommendation": ContributionRecommendationService,
}
AGENT_TASK_TYPES = {
    "literature_search": "LITERATURE_SEARCH",
    "manuscript_assistance": "MANUSCRIPT_ASSISTANCE",
    "innovation_point_generation": "INNOVATION_POINT_GENERATION",
    "academic_compliance": "ACADEMIC_COMPLIANCE",
    "academic_figure": "ACADEMIC_FIGURE",
    "arxiv_daily": "ARXIV_DAILY",
    "academic_translation": "ACADEMIC_TRANSLATION",
    "paper_reading": "PAPER_READING",
    "patent_drafting": "PATENT_DRAFTING",
    "reviewer_comments": "REVIEWER_COMMENTS",
    "contribution_recommendation": "CONTRIBUTION_RECOMMENDATION",
}


@bp.get("/", strict_slashes=False)
def list_tasks():
    """Return the current user's recent tasks for history and legacy clients."""
    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 100)
    except (TypeError, ValueError):
        limit = 50
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        offset = 0

    tasks = db.session.scalars(
        select(Task)
        .where(Task.user_id == g.current_user.id)
        .order_by(Task.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return ok(
        [serialize_task(task) for task in tasks],
        meta={"limit": limit, "offset": offset, "total": len(tasks)},
    )


# The production reverse proxy may normalize a URL to `/tasks/` while the
# local Vite proxy keeps `/tasks`.  Accept both forms directly so a POST is
# never redirected and replayed as a GET (which returns the task history).
@bp.post("", strict_slashes=False)
def create_task():
    payload = request.get_json(silent=True) or {}
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        return error("请输入研究问题或任务要求", code="PROMPT_REQUIRED")
    agent = resolve_agent(payload)
    if agent is None:
        return error("所选智能体尚未在数据库中启用", code="AGENT_NOT_AVAILABLE", status=409)
    if agent.code == "paper_reading" and not (
        payload.get("attachment_id") or extract_paper_link(payload.get("link"))
    ):
        return error("请上传 PDF 或提供有效的 arXiv 链接", code="PAPER_SOURCE_REQUIRED")
    if agent.code == "academic_compliance" and not payload.get("attachment_id"):
        return error("请上传待检测的论文稿件", code="COMPLIANCE_SOURCE_REQUIRED")
    if agent.code == "academic_translation" and not payload.get("attachment_id"):
        return error("请上传待翻译的学术文档", code="TRANSLATION_SOURCE_REQUIRED")
    if agent.code == "patent_drafting" and not payload.get("attachment_id") and len(prompt) < 50:
        return error(
            "未上传技术材料时，请至少输入 50 个字符的技术方案说明",
            code="PATENT_MATERIAL_REQUIRED",
        )
    service = resolve_agent_service(agent.code)
    if service is None:
        return error("所选智能体暂未接入任务执行服务", code="AGENT_SERVICE_NOT_AVAILABLE", status=409)

    paper_reading_options = None
    if agent.code == "paper_reading":
        try:
            paper_reading_options = normalize_paper_reading_options(payload)
        except ValueError as exc:
            return error(str(exc), code="PAPER_READING_OPTIONS_INVALID")
    innovation_options = None
    if agent.code == "innovation_point_generation":
        try:
            innovation_options = normalize_innovation_options(payload)
        except ValueError as exc:
            return error(str(exc), code="INNOVATION_OPTIONS_INVALID")
    compliance_options = None
    if agent.code == "academic_compliance":
        compliance_options = normalize_compliance_task_options(payload)
    contribution_options = None
    if agent.code == "contribution_recommendation":
        contribution_options = normalize_contribution_options(payload)
    reviewer_options = None
    if agent.code == "reviewer_comments":
        reviewer_options = normalize_reviewer_options(payload)
    translation_options = None
    if agent.code == "academic_translation":
        try:
            translation_options = normalize_translation_task_options(payload)
        except ValueError as exc:
            return error(str(exc), code="TRANSLATION_OPTIONS_INVALID")
    patent_options = None
    if agent.code == "patent_drafting":
        try:
            patent_options = normalize_patent_options(payload, prompt)
        except ValueError as exc:
            return error(str(exc), code="PATENT_OPTIONS_INVALID")
    figure_options = None
    figure_files = None
    if agent.code == "academic_figure":
        try:
            figure_options = normalize_figure_options(payload)
            figure_files = normalize_figure_files(payload)
            validate_figure_inputs(figure_options, figure_files)
        except ValueError as exc:
            return error(str(exc), code="FIGURE_OPTIONS_INVALID")
    arxiv_daily_options = None
    if agent.code == "arxiv_daily":
        try:
            arxiv_daily_options = normalize_arxiv_daily_options(payload)
        except ValueError as exc:
            return error(str(exc), code="ARXIV_DAILY_OPTIONS_INVALID")

    user = resolve_user()
    if user is None:
        return error("数据库中没有可用用户，请先完成用户初始化", code="USER_REQUIRED", status=409)
    if agent.code == "academic_translation":
        source_path = resolve_translation_upload(user.id, payload.get("attachment_id"))
        if source_path is None:
            return error(
                "待翻译文档不存在、已失效或不属于当前用户",
                code="TRANSLATION_SOURCE_INVALID",
            )
        try:
            validate_translation_inputs(translation_options or {}, source_path)
        except ValueError as exc:
            return error(str(exc), code="TRANSLATION_OPTIONS_INVALID")

    project_id = parse_optional_uuid(payload.get("project_id"))
    if payload.get("project_id") and project_id is None:
        return error("项目标识无效", code="PROJECT_ID_INVALID")
    if project_id and get_project_access(project_id, edit=True) is None:
        return error("项目不存在", code="PROJECT_NOT_FOUND", status=404)
    conversation_id = parse_optional_uuid(payload.get("conversation_id"))
    if payload.get("conversation_id") and conversation_id is None:
        return error("对话标识无效", code="CONVERSATION_ID_INVALID")
    if conversation_id:
        conversation_access = get_conversation_access(conversation_id, edit=True)
        if conversation_access is None or (project_id and conversation_access[0].project_id != project_id):
            return error("对话不存在", code="CONVERSATION_NOT_FOUND", status=404)
        project_id = project_id or conversation_access[0].project_id

    model_config_id = extract_model_config_id(payload)
    model_config = None
    if model_config_id:
        model_config = db.session.scalar(
            select(ModelConfig).where(
                ModelConfig.id == model_config_id,
                ModelConfig.owner_user_id == user.id,
                ModelConfig.config_scope == "USER",
                ModelConfig.status == "ACTIVE",
                ModelConfig.deleted_at.is_(None),
            )
        )
        if model_config is None:
            return error(
                "所选个人模型不存在、未验证或已停用",
                code="MODEL_CONFIG_NOT_AVAILABLE",
                status=409,
            )

    readiness = agent_readiness(
        current_app,
        agent.code,
        has_personal_model=model_config is not None,
        user_id=user.id,
    )
    if readiness["readiness"] == "UNAVAILABLE":
        return error(
            readiness["readiness_detail"],
            code="AGENT_DEPENDENCY_UNAVAILABLE",
            status=409,
        )

    task = Task(
        user_id=user.id,
        project_id=project_id,
        conversation_id=conversation_id,
        task_type=AGENT_TASK_TYPES.get(agent.code, agent.code.upper()),
        agent_id=agent.id,
        model_config_id=model_config.id if model_config else None,
        status="QUEUED",
        progress=0,
        current_step="等待执行",
        input_json={
            "prompt": prompt,
            "model": model_config.name if model_config else payload.get("model"),
            "attachment": payload.get("attachment"),
            "attachment_id": payload.get("attachment_id"),
            "link": payload.get("link"),
            "speed_profile": payload.get("speed_profile"),
            "paper_reading_options": paper_reading_options,
            "agent_code": agent.code,
            "innovation_options": innovation_options,
            "compliance_options": compliance_options,
            "contribution_options": contribution_options,
            "reviewer_options": reviewer_options,
            "translation_options": translation_options,
            "patent_options": patent_options,
            "figure_options": figure_options,
            "figure_files": figure_files,
            "arxiv_daily_options": arxiv_daily_options,
        },
        output_json={},
        trace_summary={},
    )
    db.session.add(task)
    db.session.commit()
    service.start(task.id, user.id)
    return ok(serialize_task(task), status=201)


@bp.get("/<task_id>")
def get_task(task_id: str):
    task = find_task(task_id)
    if task is None:
        return error("任务不存在", code="TASK_NOT_FOUND", status=404)
    return ok(serialize_task(task))


@bp.post("/<task_id>/patent-selection")
def submit_patent_selection(task_id: str):
    task = find_task(task_id)
    if task is None:
        return error("任务不存在", code="TASK_NOT_FOUND", status=404)
    if task.task_type != "PATENT_DRAFTING":
        return error("当前任务不是专利撰写任务", code="PATENT_TASK_REQUIRED", status=409)
    if task.status != "WAITING_INPUT":
        return error("当前任务不在专利点选择阶段", code="PATENT_SELECTION_NOT_AVAILABLE", status=409)
    payload = request.get_json(silent=True) or {}
    selected_id = str(payload.get("selected_id") or "").strip()
    notes = str(payload.get("notes") or "").strip()
    record = db.session.scalar(
        select(PatentDraftingRun).where(
            PatentDraftingRun.task_id == task.id,
            PatentDraftingRun.user_id == g.current_user.id,
        )
    )
    if record is None:
        return error("专利运行记录不存在", code="PATENT_RUN_NOT_FOUND", status=404)
    valid_ids = {
        str(item.get("id"))
        for item in record.candidates
        if isinstance(item, dict) and item.get("id")
    }
    if selected_id not in valid_ids:
        return error("请选择当前候选列表中的一个专利点", code="PATENT_SELECTION_INVALID")
    task.status = "QUEUED"
    task.current_step = "正在提交专利点选择"
    record.status = "QUEUED"
    db.session.commit()
    service = resolve_agent_service("patent_drafting")
    service.resume(task.id, g.current_user.id, selected_id, notes)
    return ok(serialize_task(task), status=202)


@bp.get("/<task_id>/events")
def task_events(task_id: str):
    task = find_task(task_id)
    if task is None:
        return error("任务不存在", code="TASK_NOT_FOUND", status=404)
    last_sequence = max(int(request.headers.get("Last-Event-ID", "0") or 0), 0)
    stream_task_id = task.id

    @stream_with_context
    def generate():
        nonlocal last_sequence
        idle_after_terminal = 0
        while True:
            events = db.session.scalars(
                select(TaskEvent)
                .where(TaskEvent.task_id == stream_task_id, TaskEvent.sequence > last_sequence)
                .order_by(TaskEvent.sequence)
            ).all()
            for event in events:
                last_sequence = event.sequence
                payload = {"sequence": event.sequence, "type": event.event_type, **event.payload}
                yield (
                    f"id: {event.sequence}\n"
                    f"event: {event.event_type}\n"
                    f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                )
            db.session.expire_all()
            current = db.session.get(Task, stream_task_id)
            if current is None or current.status in STREAM_END_STATES:
                idle_after_terminal = idle_after_terminal + 1 if not events else 0
                if idle_after_terminal >= 2:
                    break
            # Hand the connection back to the pool while waiting; a long task
            # must not pin one of the few pooled connections per open browser tab.
            db.session.remove()
            time.sleep(0.25)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@bp.get("/<task_id>/fishbone")
def task_fishbone(task_id: str):
    task = find_task(task_id)
    if task is None:
        return error("任务不存在", code="TASK_NOT_FOUND", status=404)
    path: Path = current_app.config["AGENT_GENERATED_DIR"] / str(task.id) / "annual_publication_timeline.png"
    if not path.exists():
        return error("年度脉络图尚未生成", code="ARTIFACT_NOT_READY", status=404)
    return send_file(path, mimetype="image/png", max_age=0)


@bp.get("/<task_id>/artifacts/<artifact_kind>")
def task_artifact(task_id: str, artifact_kind: str):
    task = find_task(task_id)
    if task is None:
        return error("任务不存在", code="TASK_NOT_FOUND", status=404)
    artifact_keys = {
        "compliance-report": ("report_markdown", "text/markdown", "compliance-report.md", "AGENT_GENERATED_DIR"),
        "compliance-json": ("result_json", "application/json", "compliance-result.json", "AGENT_GENERATED_DIR"),
        "translation-preview": ("pdf_monolingual", "application/pdf", "translation.pdf", "AGENT_GENERATED_DIR"),
        "translation-pdf": ("pdf_monolingual", "application/pdf", "translation.pdf", "AGENT_GENERATED_DIR"),
        "translation-bilingual-pdf": ("pdf_bilingual", "application/pdf", "translation-bilingual.pdf", "AGENT_GENERATED_DIR"),
        "translation-markdown": ("monolingual_markdown", "text/markdown", "translation.md", "AGENT_GENERATED_DIR"),
        "translation-bilingual-markdown": ("bilingual_markdown", "text/markdown", "translation-bilingual.md", "AGENT_GENERATED_DIR"),
        "translation-docx": ("monolingual_docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "translation.docx", "AGENT_GENERATED_DIR"),
        "translation-report": ("translation_report", "application/json", "translation-report.json", "AGENT_GENERATED_DIR"),
        "patent-disclosure-markdown": ("patent-disclosure-markdown", "text/markdown", "patent-disclosure.md", "PATENT_DRAFTING_DATA_DIR"),
        "patent-disclosure-docx": ("patent-disclosure-docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "patent-disclosure.docx", "PATENT_DRAFTING_DATA_DIR"),
        "patent-claims-markdown": ("patent-claims-markdown", "text/markdown", "patent-claims.md", "PATENT_DRAFTING_DATA_DIR"),
        "patent-claims-json": ("patent-claims-json", "application/json", "patent-claims.json", "PATENT_DRAFTING_DATA_DIR"),
        "patent-manifest": ("patent-manifest", "application/json", "patent-manifest.json", "PATENT_DRAFTING_DATA_DIR"),
        "patent-disclosure-evidence": ("patent-disclosure-evidence", "text/markdown", "patent-disclosure-evidence.md", "PATENT_DRAFTING_DATA_DIR"),
        "patent-claim-evidence": ("patent-claim-evidence", "text/markdown", "patent-claim-evidence.md", "PATENT_DRAFTING_DATA_DIR"),
        "figure-zh-png": ("figure-zh-png", "image/png", "figure_zh.png", "ACADEMIC_FIGURE_DATA_DIR"),
        "figure-zh-svg": ("figure-zh-svg", "image/svg+xml", "figure_zh.svg", "ACADEMIC_FIGURE_DATA_DIR"),
        "figure-zh-pdf": ("figure-zh-pdf", "application/pdf", "figure_zh.pdf", "ACADEMIC_FIGURE_DATA_DIR"),
        "figure-en-png": ("figure-en-png", "image/png", "figure_en.png", "ACADEMIC_FIGURE_DATA_DIR"),
        "figure-en-svg": ("figure-en-svg", "image/svg+xml", "figure_en.svg", "ACADEMIC_FIGURE_DATA_DIR"),
        "figure-en-pdf": ("figure-en-pdf", "application/pdf", "figure_en.pdf", "ACADEMIC_FIGURE_DATA_DIR"),
        "figure-code-python": ("figure-code-python", "text/x-python", "figure.py", "ACADEMIC_FIGURE_DATA_DIR"),
        "figure-code-r": ("figure-code-r", "text/plain", "figure.R", "ACADEMIC_FIGURE_DATA_DIR"),
        "figure-code-latex": ("figure-code-latex", "text/plain", "figure.tex", "ACADEMIC_FIGURE_DATA_DIR"),
        "figure-code-mermaid": ("figure-code-mermaid", "text/plain", "figure.mmd", "ACADEMIC_FIGURE_DATA_DIR"),
        "figure-caption-zh": ("figure-caption-zh", "text/plain", "caption-zh.txt", "ACADEMIC_FIGURE_DATA_DIR"),
        "figure-caption-en": ("figure-caption-en", "text/plain", "caption-en.txt", "ACADEMIC_FIGURE_DATA_DIR"),
        "figure-source-data": ("figure-source-data", "text/csv", "source-data.csv", "ACADEMIC_FIGURE_DATA_DIR"),
        "figure-config": ("figure-config", "application/json", "figure-config.json", "ACADEMIC_FIGURE_DATA_DIR"),
        "figure-quality": ("figure-quality", "application/json", "figure-quality.json", "ACADEMIC_FIGURE_DATA_DIR"),
        "figure-execution": ("figure-execution", "application/json", "figure-execution.json", "ACADEMIC_FIGURE_DATA_DIR"),
        "figure-manifest": ("figure-manifest", "application/json", "figure-manifest.json", "ACADEMIC_FIGURE_DATA_DIR"),
        "figure-request": ("figure-request", "application/json", "figure-request.json", "ACADEMIC_FIGURE_DATA_DIR"),
        "literature-ppt": ("literature-ppt", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "literature-presentation.pptx", "LITERATURE_PPT_DATA_DIR"),
        "literature-evidence": ("literature-evidence", "application/json", "literature.evidence.json", "LITERATURE_PPT_DATA_DIR"),
    }
    definition = artifact_keys.get(artifact_kind)
    if definition is None:
        return error("任务产物不存在", code="ARTIFACT_NOT_FOUND", status=404)
    key, mimetype, download_name, root_config = definition
    raw_path = ((task.output_json or {}).get("artifacts") or {}).get(key)
    if not raw_path:
        return error("任务产物尚未生成", code="ARTIFACT_NOT_READY", status=404)
    generated_root = Path(current_app.config[root_config]).resolve()
    path = Path(str(raw_path)).resolve()
    # Older tasks may contain an absolute path from a previous deployment
    # root. For figure artifacts, resolve the stable user/task bundle layout
    # under the current configured data directory as a portable fallback.
    if root_config == "ACADEMIC_FIGURE_DATA_DIR" and (
        not path.is_file() or not path.is_relative_to(generated_root)
    ):
        portable = generated_root / str(task.user_id) / str(task.id) / "bundle" / download_name
        if portable.is_file():
            path = portable.resolve()
    if not path.is_file() or not path.is_relative_to(generated_root):
        return error("任务产物不可用", code="ARTIFACT_NOT_READY", status=404)
    return send_file(
        path,
        mimetype=mimetype,
        as_attachment=artifact_kind not in {
            "translation-preview",
            "figure-zh-png",
            "figure-zh-svg",
            "figure-en-png",
            "figure-en-svg",
        },
        download_name=download_name,
        max_age=0,
    )


def resolve_agent(payload: dict) -> Agent | None:
    agent_id = payload.get("agent_id")
    if agent_id:
        try:
            agent = db.session.get(Agent, UUID(str(agent_id)))
            if agent and agent.status == "ACTIVE":
                return agent
        except ValueError:
            pass
    code = str(payload.get("agent_code") or "literature_search")
    return db.session.scalar(select(Agent).where(Agent.code == code, Agent.status == "ACTIVE"))


def resolve_agent_service(agent_code: str):
    service_key = f"{agent_code}_service"
    service = current_app.extensions.get(service_key)
    if service is not None:
        return service
    service_class = AGENT_SERVICE_CLASSES.get(agent_code)
    if service_class is None:
        return None
    service = service_class(current_app._get_current_object())
    current_app.extensions[service_key] = service
    return service


def resolve_user() -> User | None:
    return g.current_user


def find_task(task_id: str) -> Task | None:
    try:
        task = db.session.get(Task, UUID(task_id))
    except ValueError:
        return None
    if task is None:
        return None
    if task.user_id != g.current_user.id and g.current_user.role_code != "system_admin":
        return None
    return task


def serialize_task(task: Task) -> dict:
    output = serialize_task_output(task)
    error_detail = output.get("task_error") if isinstance(output.get("task_error"), dict) else None
    return {
        "id": str(task.id),
        # Keep aliases for clients from the pre-envelope API.  New clients
        # should use `id`; the aliases make rolling upgrades safe while a
        # cached frontend or a second server instance is still in circulation.
        "task_id": str(task.id),
        "taskId": str(task.id),
        "user_id": str(task.user_id),
        "project_id": str(getattr(task, "project_id", None)) if getattr(task, "project_id", None) else None,
        "conversation_id": str(getattr(task, "conversation_id", None)) if getattr(task, "conversation_id", None) else None,
        "agent_id": str(task.agent_id) if task.agent_id else None,
        "agent_team_id": str(getattr(task, "agent_team_id", None)) if getattr(task, "agent_team_id", None) else None,
        "model_config_id": str(getattr(task, "model_config_id", None)) if getattr(task, "model_config_id", None) else None,
        "task_type": task.task_type,
        "title": str((task.input_json or {}).get("prompt") or "")[:48],
        "prompt": (task.input_json or {}).get("prompt"),
        "status": task.status,
        "progress": task.progress,
        "current_step": task.current_step,
        "output": output,
        "error": task.safe_error_message,
        "error_detail": error_detail,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if getattr(task, "started_at", None) else None,
        "finished_at": task.finished_at.isoformat() if getattr(task, "finished_at", None) else None,
        "updated_at": task.updated_at.isoformat() if getattr(task, "updated_at", None) else None,
    }


def parse_optional_uuid(value) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def serialize_task_output(task: Task) -> dict:
    output = dict(task.output_json or {})
    if task.task_type == "ACADEMIC_TRANSLATION":
        return serialize_translation_task_output(task, output)
    if task.task_type != "ACADEMIC_FIGURE":
        return output

    artifacts = output.get("artifacts")
    if isinstance(artifacts, dict):
        output["artifacts"] = {str(kind): str(kind) for kind in artifacts}
    output.pop("figure_artifacts", None)

    dataset = output.get("dataset_summary")
    if isinstance(dataset, dict):
        sanitized_dataset = dict(dataset)
        sanitized_dataset["normalized_path"] = None
        sanitized_dataset["source_files"] = [
            Path(str(item)).name for item in dataset.get("source_files") or []
        ]
        output["dataset_summary"] = sanitized_dataset

    figure_request = output.get("figure_request")
    if isinstance(figure_request, dict):
        sanitized_request = dict(figure_request)
        sanitized_request.pop("output_dir", None)
        for key in ("data_files", "context_files", "sketch_files"):
            sanitized_request[key] = [
                Path(str(item)).name for item in figure_request.get(key) or []
            ]
        output["figure_request"] = sanitized_request

    quality = output.get("figure_quality")
    if isinstance(quality, dict):
        sanitized_quality = dict(quality)
        sanitized_quality["generated_files"] = [
            Path(str(item)).name for item in quality.get("generated_files") or []
        ]
        output["figure_quality"] = sanitized_quality
    return output


def serialize_translation_task_output(task: Task, output: dict) -> dict:
    input_json = task.input_json or {}
    stored_request = output.get("translation_request")
    request_payload = dict(stored_request) if isinstance(stored_request, dict) else {}

    raw_options = input_json.get("translation_options")
    normalized_options = {}
    if isinstance(raw_options, dict):
        try:
            normalized_options = normalize_translation_options(raw_options)
        except ValueError:
            normalized_options = {}

    attachment_name = str(input_json.get("attachment") or request_payload.get("file_name") or "").strip()
    file_type = str(request_payload.get("file_type") or "").strip().lower()
    if not file_type and attachment_name:
        file_type = Path(attachment_name).suffix.removeprefix(".").lower()

    if normalized_options or request_payload:
        output["translation_request"] = {
            "file_name": attachment_name or request_payload.get("file_name"),
            "file_type": file_type or request_payload.get("file_type"),
            **normalized_options,
            **request_payload,
        }
        request = output["translation_request"]
        output["translation_restore"] = {
            "prompt": input_json.get("prompt"),
            "attachment": input_json.get("attachment"),
            "attachment_id": input_json.get("attachment_id"),
            "file_name": request.get("file_name"),
            "file_type": request.get("file_type"),
            "source_lang": request.get("source_lang"),
            "target_lang": request.get("target_lang"),
            "precision": request.get("precision"),
            "glossary": request.get("glossary") or {},
            "domain": request.get("domain"),
            "parallel": request.get("parallel"),
            "preserve_pdf_layout": bool(request.get("preserve_pdf_layout")),
            "bilingual": bool(request.get("bilingual")),
            "translate_figures": bool(request.get("translate_figures")),
            "pdf_layout_mode": request.get("pdf_layout_mode"),
            "pdf_timeout_seconds": request.get("pdf_timeout_seconds"),
        }

    warning_items = output.get("translation_warning_items")
    if not isinstance(warning_items, list):
        warning_items = normalize_translation_warning_items(output.get("translation_warnings"))
        if warning_items:
            output["translation_warning_items"] = warning_items

    runtime = output.get("translation_runtime")
    runtime_payload = dict(runtime) if isinstance(runtime, dict) else {}
    runtime_payload["status"] = task.status
    if warning_items and "warning_count" not in runtime_payload:
        runtime_payload["warning_count"] = len(warning_items)
    if task.status == "FAILED":
        task_error = output.get("task_error")
        if isinstance(task_error, dict):
            runtime_payload.update(
                {
                    "outcome": "failed",
                    "feedback_kind": "failure",
                    "error_kind": task_error.get("kind"),
                    "error_message": task_error.get("message"),
                    "next_action": task_error.get("next_action"),
                }
            )
    elif task.status == "SUCCEEDED":
        runtime_payload.setdefault("outcome", "warning" if warning_items else "success")
        runtime_payload.setdefault("feedback_kind", "warning" if warning_items else "success")
    else:
        runtime_payload.setdefault("outcome", "running")
        runtime_payload.setdefault("feedback_kind", "progress")
    output["translation_runtime"] = runtime_payload
    return output


def extract_paper_link(value: object) -> bool:
    text_value = str(value or "").lower()
    return "arxiv.org/abs/" in text_value or "arxiv.org/pdf/" in text_value or text_value.startswith("arxiv:")


def extract_model_config_id(payload: dict) -> UUID | None:
    raw_value = payload.get("model_config_id")
    if not raw_value:
        model_value = str(payload.get("model") or "")
        if model_value.startswith("model_config:"):
            raw_value = model_value.split(":", 1)[1]
    if not raw_value:
        return None
    try:
        return UUID(str(raw_value))
    except ValueError:
        return UUID(int=0)


def normalize_innovation_options(payload: dict) -> dict:
    mode = str(payload.get("innovation_mode") or "full").strip().lower()
    if mode not in {"full", "expand", "evaluate"}:
        raise ValueError("创新生成模式无效")
    try:
        top_k = int(payload.get("innovation_top_k") or 5)
    except (TypeError, ValueError) as exc:
        raise ValueError("创新点数量必须是整数") from exc
    if not 1 <= top_k <= 10:
        raise ValueError("创新点数量必须在 1 到 10 之间")

    def clean_list(value: object, limit: int) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip()[:200] for item in value if str(item).strip()][:limit]

    time_range = str(payload.get("innovation_time_range") or "").strip()[:32]
    constraints = payload.get("innovation_constraints")
    if constraints is not None and not isinstance(constraints, dict):
        raise ValueError("创新约束必须是 JSON 对象")
    return {
        "mode": mode,
        "top_k": top_k,
        "time_range": time_range or None,
        "keywords": clean_list(payload.get("innovation_keywords"), 12),
        "seed_ideas": clean_list(payload.get("innovation_seed_ideas"), 10),
        "constraints": constraints or {},
        "additional_context": str(payload.get("innovation_additional_context") or "").strip()[:2000],
    }


def normalize_compliance_task_options(payload: dict) -> dict:
    task_type = str(payload.get("compliance_task_type") or "paper_precheck").strip()
    if task_type not in {"paper_precheck", "journal_submission"}:
        task_type = "paper_precheck"
    target_rule_set = str(payload.get("compliance_rule_set") or "default").strip()[:64]
    return {"task_type": task_type, "target_rule_set": target_rule_set or "default"}


def normalize_contribution_options(payload: dict) -> dict:
    raw_levels = payload.get("contribution_target_levels")
    if isinstance(raw_levels, list):
        levels = [str(item).strip() for item in raw_levels if str(item).strip()][:3]
    else:
        levels = [item.strip() for item in str(raw_levels or "CCF-A,CCF-B").split(",") if item.strip()][:3]
    novelty = str(payload.get("contribution_novelty_level") or "substantial").strip()
    if novelty not in {"incremental", "substantial", "breakthrough"}:
        novelty = "substantial"

    def as_float(name: str, default: float) -> float:
        try:
            value = float(payload.get(name) or default)
        except (TypeError, ValueError):
            value = default
        return max(0.0, min(value, 1.0))

    try:
        max_review_weeks = int(payload.get("contribution_max_review_weeks") or 12)
    except (TypeError, ValueError):
        max_review_weeks = 12
    return {
        "target_ccf_levels": levels or ["CCF-A", "CCF-B"],
        "max_review_weeks": max(4, min(max_review_weeks, 52)),
        "prefer_oa": bool(payload.get("contribution_prefer_oa", True)),
        "novelty_level": novelty,
        "experiment_completeness": as_float("contribution_experiment_completeness", 0.72),
        "theoretical_rigor": as_float("contribution_theoretical_rigor", 0.75),
        "writing_quality": as_float("contribution_writing_quality", 0.75),
    }


def normalize_reviewer_options(payload: dict) -> dict:
    mode = str(payload.get("reviewer_reply_mode") or "full").strip()
    if mode not in {"full", "analysis", "reply"}:
        mode = "full"
    return {
        "mode": mode,
        "target_language": str(payload.get("reviewer_target_language") or "zh").strip()[:16],
    }


def normalize_translation_task_options(payload: dict) -> dict:
    glossary = payload.get("translation_glossary")
    if isinstance(glossary, str):
        try:
            glossary = json.loads(glossary or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("术语表必须是有效 JSON 对象") from exc
    if glossary is not None and not isinstance(glossary, dict):
        raise ValueError("术语表必须是源术语到目标术语的 JSON 对象")
    return normalize_translation_options(
        {
            "source_lang": payload.get("translation_source_lang"),
            "target_lang": payload.get("translation_target_lang"),
            "precision": payload.get("translation_precision"),
            "glossary": glossary or {},
            "domain": payload.get("translation_domain"),
            "parallel": payload.get("translation_parallel"),
            "preserve_pdf_layout": payload.get("translation_preserve_pdf_layout", False),
            "bilingual": payload.get("translation_bilingual", False),
            "translate_figures": payload.get("translation_translate_figures", False),
            "pdf_layout_mode": payload.get("translation_pdf_layout_mode"),
            "pdf_timeout_seconds": payload.get("translation_pdf_timeout_seconds"),
        }
    )


def validate_translation_inputs(options: dict, source_path: Path) -> None:
    if options.get("preserve_pdf_layout") and source_path.suffix.lower() != ".pdf":
        raise ValueError("保留 PDF 原版式仅适用于 PDF 文件")


def normalize_patent_options(payload: dict, prompt: str) -> dict:
    workflow_mode = str(payload.get("patent_workflow_mode") or "flow_first").strip()
    if workflow_mode not in {"flow_first", "strict"}:
        raise ValueError("专利撰写工作流模式无效")
    title = str(payload.get("patent_title") or prompt[:120]).strip()
    if not title:
        raise ValueError("请填写专利技术方案名称")
    return {
        "title": title[:200],
        "workflow_mode": workflow_mode,
    }


def normalize_figure_options(payload: dict) -> dict:
    figure_type = str(payload.get("figure_type") or "auto").strip().lower()
    allowed_types = {"auto", "line", "bar", "scatter", "box", "heatmap", "flowchart", "image_panel"}
    if figure_type not in allowed_types:
        raise ValueError("图表类型无效")
    planning_mode = str(payload.get("figure_planning_mode") or "online").strip().lower()
    if planning_mode not in {"online", "offline"}:
        raise ValueError("绘图规划模式无效")

    def normalize_list(name: str, default: list[str], allowed: set[str]) -> list[str]:
        raw = payload.get(name)
        values = raw if isinstance(raw, list) else default
        normalized = list(dict.fromkeys(str(item).strip().lower() for item in values if str(item).strip()))
        if not normalized or set(normalized) - allowed:
            raise ValueError(f"{name} 包含不支持的选项")
        return normalized

    code_formats = normalize_list(
        "figure_code_formats",
        ["python", "r", "latex", "mermaid"],
        {"python", "r", "latex", "mermaid"},
    )
    if "python" not in code_formats:
        code_formats.insert(0, "python")
    return {
        "figure_type": figure_type,
        "planning_mode": planning_mode,
        "export_formats": normalize_list(
            "figure_export_formats", ["pdf", "svg", "png"], {"pdf", "svg", "png"}
        ),
        "code_formats": code_formats,
        "languages": normalize_list("figure_languages", ["zh", "en"], {"zh", "en"}),
    }


def normalize_figure_files(payload: dict) -> list[dict[str, str]]:
    raw_items = payload.get("figure_files")
    if not isinstance(raw_items, list):
        raw_items = []
    if not raw_items and payload.get("attachment_id"):
        raw_items = [
            {
                "upload_id": payload.get("attachment_id"),
                "file_name": payload.get("attachment"),
                "kind": payload.get("figure_file_kind") or "context",
            }
        ]
    if len(raw_items) > 12:
        raise ValueError("单个绘图任务最多上传 12 个文件")
    normalized = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError("绘图文件描述必须是对象")
        kind = str(item.get("kind") or "").strip().lower()
        upload_id = str(item.get("upload_id") or item.get("uploadId") or "").strip()
        file_name = Path(str(item.get("file_name") or item.get("fileName") or "")).name
        if kind not in {"data", "context", "sketch"} or not upload_id or not file_name:
            raise ValueError("绘图文件描述缺少有效的类型、上传编号或文件名")
        normalized.append({"kind": kind, "upload_id": upload_id, "file_name": file_name})
    return normalized


def validate_figure_inputs(options: dict, files: list[dict[str, str]]) -> None:
    kinds = {item["kind"] for item in files}
    figure_type = options["figure_type"]
    if figure_type in {"line", "bar", "scatter", "box", "heatmap"} and "data" not in kinds:
        raise ValueError("统计图必须上传 CSV、TSV、Excel 或 JSON 数据文件")
    if figure_type == "image_panel" and "sketch" not in kinds:
        raise ValueError("图片拼版必须上传至少一张草图或实验图片")
