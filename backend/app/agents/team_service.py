from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from flask import Flask
from sqlalchemy import func, select

from ..extensions import db
from ..llm import run_openai_compatible_chat
from ..models import Agent, AgentTeam, Task, TaskEvent
from .task_service import BuiltinAgentTaskService, public_error_message, to_jsonable


DIRECT_AGENT_CODES = {
    "literature_search",
    "manuscript_assistance",
    "innovation_point_generation",
    "reviewer_comments",
    "contribution_recommendation",
}

AGENT_TASK_TYPES = {
    "literature_search": "LITERATURE_SEARCH",
    "manuscript_assistance": "MANUSCRIPT_ASSISTANCE",
    "innovation_point_generation": "INNOVATION_POINT_GENERATION",
    "paper_reading": "PAPER_READING",
    "academic_compliance": "ACADEMIC_COMPLIANCE",
    "academic_translation": "ACADEMIC_TRANSLATION",
    "academic_figure": "ACADEMIC_FIGURE",
    "arxiv_daily": "ARXIV_DAILY",
    "reviewer_comments": "REVIEWER_COMMENTS",
    "contribution_recommendation": "CONTRIBUTION_RECOMMENDATION",
    "patent_drafting": "PATENT_DRAFTING",
}

AGENT_ROUTES = {
    "paper_reading": "/agents/paper-reading",
    "academic_compliance": "/agents/academic-compliance",
    "academic_translation": "/agents/academic-translation",
    "academic_figure": "/agents/academic-figure",
    "arxiv_daily": "/agents/academic-daily",
    "patent_drafting": "/agents/patent-drafting",
}

INPUT_REQUIREMENTS = {
    "paper_reading": "需要上传 PDF 或提供 arXiv 链接",
    "academic_compliance": "需要上传待检测的论文稿件",
    "academic_translation": "需要上传待翻译的学术文档",
    "academic_figure": "需要上传数据、上下文或草图文件",
    "arxiv_daily": "需要选择学科分类和检索选项",
    "patent_drafting": "需要上传技术材料或提供不少于 50 字的技术说明",
}


class AgentTeamService(BuiltinAgentTaskService):
    agent_label = "agent-team"
    failed_message = "智囊团协作任务执行失败"

    def __init__(self, app: Flask, resolve_service) -> None:
        super().__init__(app)
        self.resolve_service = resolve_service

    def run(self, task_id: UUID, user_id: UUID) -> None:
        parent = db.session.get(Task, task_id)
        if parent is None or parent.agent_team_id is None:
            return
        team = db.session.get(AgentTeam, parent.agent_team_id)
        if team is None:
            raise RuntimeError("智囊团配置不存在")

        members = normalize_member_codes((team.team_config or {}).get("members"))
        if not members:
            raise RuntimeError("智囊团未配置成员")

        parent.status = "RUNNING"
        parent.started_at = datetime.now(UTC)
        self.emit(parent, "task.started", 3, f"已启动 {team.name}，共 {len(members)} 个协作阶段")

        stages: list[dict[str, Any]] = []
        previous_context = ""
        for index, code in enumerate(members):
            agent = db.session.scalar(select(Agent).where(Agent.code == code, Agent.status == "ACTIVE"))
            if agent is None:
                stages.append(stage_payload(code, code, "FAILED", message="Agent 未启用"))
                return self.finish_failed(parent, stages, f"成员 {code} 未启用")

            if code not in DIRECT_AGENT_CODES:
                stage = stage_payload(
                    code,
                    agent.name,
                    "WAITING_INPUT",
                    message=INPUT_REQUIREMENTS.get(code, "需要补充专属任务参数"),
                    route=AGENT_ROUTES.get(code),
                )
                stages.append(stage)
                parent.status = "WAITING_INPUT"
                parent.current_step = f"{agent.name} 等待补充材料"
                parent.progress = max(5, int(index / len(members) * 100))
                parent.output_json = {**(parent.output_json or {}), "stages": stages, "waiting_stage": stage}
                parent.trace_summary = {"team_id": str(team.id), "completed_stages": index, "waiting_agent": code}
                db.session.commit()
                self.emit(
                    parent,
                    "team.waiting_input",
                    parent.progress,
                    f"{agent.name} 需要补充材料后单独执行",
                    stage=stage,
                )
                return

            child_prompt = build_member_prompt(parent, agent.name, previous_context)
            child = Task(
                user_id=user_id,
                project_id=parent.project_id,
                conversation_id=parent.conversation_id,
                task_type=AGENT_TASK_TYPES.get(code, code.upper()),
                agent_id=agent.id,
                agent_team_id=team.id,
                model_config_id=parent.model_config_id,
                status="QUEUED",
                progress=0,
                current_step="等待执行",
                input_json=build_child_input(parent, code, child_prompt),
                output_json={},
                trace_summary={"parent_task_id": str(parent.id), "team_id": str(team.id), "stage_index": index},
            )
            db.session.add(child)
            db.session.commit()
            stage = stage_payload(code, agent.name, "RUNNING", task_id=str(child.id), message="正在执行")
            stages.append(stage)
            self.update_parent(parent, stages, index, len(members), f"{agent.name} 正在协作")

            service = self.resolve_service(code)
            if service is None:
                child.status = "FAILED"
                child.safe_error_message = "Agent 执行服务不可用"
                db.session.commit()
            else:
                service.start(child.id, user_id)
                self.wait_for_child(child.id)

            db.session.expire_all()
            child = db.session.get(Task, child.id)
            if child is None:
                return self.finish_failed(parent, stages, f"{agent.name} 子任务丢失")
            stage["status"] = child.status
            stage["message"] = child.current_step or child.safe_error_message or child.status
            stage["summary"] = summarize_output(child.output_json or {})
            stage["finished_at"] = child.finished_at.isoformat() if child.finished_at else None
            if child.status != "SUCCEEDED":
                return self.finish_failed(parent, stages, f"{agent.name} 执行失败：{child.safe_error_message or child.current_step}")
            previous_context = build_handoff_context(agent.name, child.output_json or {})
            self.update_parent(parent, stages, index + 1, len(members), f"{agent.name} 已完成，准备下一阶段")

        final_summary = synthesize_team_result(
            str((parent.input_json or {}).get("prompt") or ""), team.name, stages
        )
        parent.status = "SUCCEEDED"
        parent.progress = 100
        parent.current_step = "智囊团协作完成"
        parent.finished_at = datetime.now(UTC)
        parent.output_json = {
            **(parent.output_json or {}),
            "team": {"id": str(team.id), "name": team.name},
            "stages": stages,
            "final_summary": final_summary,
        }
        parent.trace_summary = {"team_id": str(team.id), "completed_stages": len(stages), "member_count": len(members)}
        db.session.commit()
        self.emit(parent, "task.completed", 100, "智囊团已完成全部协作阶段", final_summary=final_summary)

    def wait_for_child(self, child_id: UUID) -> None:
        deadline = time.monotonic() + float(self.app.config.get("AGENT_TEAM_STAGE_TIMEOUT_SECONDS", 3600))
        while time.monotonic() < deadline:
            db.session.expire_all()
            child = db.session.get(Task, child_id)
            if child is None or child.status in {"SUCCEEDED", "FAILED", "CANCELED", "WAITING_INPUT"}:
                return
            time.sleep(0.5)
        child = db.session.get(Task, child_id)
        if child and child.status not in {"SUCCEEDED", "FAILED", "CANCELED"}:
            child.status = "FAILED"
            child.progress = 100
            child.safe_error_message = "Agent 协作阶段执行超时"
            child.finished_at = datetime.now(UTC)
            db.session.commit()

    def update_parent(self, parent: Task, stages: list[dict[str, Any]], completed: int, total: int, message: str) -> None:
        parent.progress = min(95, 5 + int(completed / total * 88))
        parent.current_step = message
        parent.output_json = {**(parent.output_json or {}), "stages": stages}
        db.session.commit()
        self.emit(parent, "team.stage", parent.progress, message, stages=stages)

    def finish_failed(self, parent: Task, stages: list[dict[str, Any]], message: str) -> None:
        parent.status = "FAILED"
        parent.progress = 100
        parent.current_step = "智囊团协作中止"
        parent.safe_error_message = public_error_message(RuntimeError(message), "智囊团协作任务执行失败")
        parent.finished_at = datetime.now(UTC)
        parent.output_json = {**(parent.output_json or {}), "stages": stages}
        db.session.commit()
        self.emit(parent, "task.failed", 100, "智囊团协作中止", error=parent.safe_error_message, stages=stages)


def normalize_member_codes(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        raw_code = item.get("code") if isinstance(item, dict) else item
        code = str(raw_code or "").strip()
        if code and code not in result:
            result.append(code)
    return result[:12]


def build_child_input(parent: Task, code: str, prompt: str) -> dict[str, Any]:
    source = parent.input_json or {}
    payload = {
        "prompt": prompt,
        "model": source.get("model") or "vertical_domain",
        "agent_code": code,
        "team_parent_task_id": str(parent.id),
    }
    if code == "innovation_point_generation":
        payload["innovation_options"] = {"mode": "full", "top_k": 5, "additional_context": prompt}
    elif code == "reviewer_comments":
        payload["reviewer_options"] = {"mode": "full", "target_language": "zh"}
    elif code == "contribution_recommendation":
        payload["contribution_options"] = {
            "target_ccf_levels": ["CCF-A", "CCF-B"],
            "max_review_weeks": 12,
            "prefer_oa": True,
            "novelty_level": "substantial",
            "experiment_completeness": 0.72,
            "theoretical_rigor": 0.75,
            "writing_quality": 0.75,
        }
    return payload


def build_member_prompt(parent: Task, agent_name: str, context: str) -> str:
    goal = str((parent.input_json or {}).get("prompt") or "").strip()
    if not context:
        return goal
    return f"智囊团总体目标：\n{goal}\n\n上一阶段交接摘要：\n{context}\n\n请以{agent_name}的职责继续完成当前阶段。"


def build_handoff_context(agent_name: str, output: dict[str, Any]) -> str:
    summary = summarize_output(output)
    return f"{agent_name}已完成。结构化结果摘要：{json.dumps(summary, ensure_ascii=False, default=str)}"


def summarize_output(output: dict[str, Any]) -> dict[str, Any]:
    preferred = (
        "query_plan", "papers", "report_markdown", "research_trends", "research_gaps", "innovations",
        "manuscript_plan", "manuscript_markdown", "review_items", "reply_strategy", "response_letter_markdown",
        "recommendations", "submission_strategy", "final_report", "metrics", "warnings", "errors",
    )
    result: dict[str, Any] = {}
    for key in preferred:
        if key not in output:
            continue
        value = to_jsonable(output[key])
        if isinstance(value, str):
            value = value[:3000]
        elif isinstance(value, list):
            value = value[:8]
        result[key] = value
    if not result:
        for key, value in list(output.items())[:6]:
            result[str(key)] = to_jsonable(value)
    return result


def synthesize_team_result(goal: str, team_name: str, stages: list[dict[str, Any]]) -> str:
    stage_text = "\n\n".join(
        f"### {stage['name']}\n{json.dumps(stage.get('summary') or {}, ensure_ascii=False, default=str)[:4500]}"
        for stage in stages
    )
    try:
        response = run_openai_compatible_chat(
            messages=[
                {"role": "system", "content": "你是多智能体科研总协调员。整合各阶段真实结果，输出简洁的 Markdown 总结，包含核心发现、可执行建议、风险与下一步；不得补造数据或引用。"},
                {"role": "user", "content": f"智囊团：{team_name}\n总体目标：{goal}\n\n阶段结果：\n{stage_text}"},
            ],
            model="vertical_domain",
            max_tokens=1800,
        )
        content = str(response.get("content") or "").strip()
        if content:
            return content
    except Exception:  # noqa: BLE001
        pass
    return f"## {team_name}协作结果\n\n总体目标：{goal}\n\n{stage_text}"


def stage_payload(code: str, name: str, status: str, **updates: Any) -> dict[str, Any]:
    return {"code": code, "name": name, "status": status, "task_id": None, "summary": {}, **updates}
