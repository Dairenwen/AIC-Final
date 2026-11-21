from __future__ import annotations

import uuid
from typing import Any

from knowledge_base_runtime.backend.dao.database import get_db, utc_now
from knowledge_base_runtime.backend.service.audit import record_audit_log
from knowledge_base_runtime.backend.utils.common import dumps, loads_dict, loads_list


def invoke_agent(payload: dict[str, Any], user_id: str, ip: str | None = None) -> dict[str, Any]:
    agent_type = str(payload.get("agent_type") or "literature_review")
    paper_ids = payload.get("paper_ids") or payload.get("paper_uids") or []
    extra_params = payload.get("extra_params") or {}
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    now = utc_now()
    result = _build_placeholder_result(agent_type, paper_ids, extra_params)
    with get_db() as db:
        db.execute(
            """
            INSERT INTO agent_jobs(job_id, agent_type, paper_ids, extra_params, status, result, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, agent_type, dumps(paper_ids), dumps(extra_params), "SUCCESS", result, now, now),
        )
        record_audit_log(
            db,
            operate_user_id=user_id,
            user_ip=ip,
            operate_type="AGENT",
            operate_sub_type="AGENT_INVOKE",
            target_resource_type="agent_job",
            target_resource_id=job_id,
            resource_title=f"{agent_type} / {len(paper_ids)} 篇论文",
            operate_content={
                "agent_type": agent_type,
                "paper_ids": paper_ids,
                "chunk_ids": payload.get("chunk_ids") or [],
                "prompt": str(extra_params.get("prompt") or extra_params.get("focus") or "")[:500],
            },
            is_system_op=False,
        )
    return {"job_id": job_id, "status": "SUCCESS"}


def get_agent_status(job_id: str) -> dict[str, Any] | None:
    with get_db() as db:
        row = db.execute("SELECT * FROM agent_jobs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["paper_ids"] = loads_list(item.get("paper_ids"))
    item["extra_params"] = loads_dict(item.get("extra_params"))
    return item


def _build_placeholder_result(agent_type: str, paper_ids: list, extra_params: dict) -> str:
    focus = extra_params.get("focus") or extra_params.get("prompt") or "通用分析"
    return f"已接收 {agent_type} 任务，论文数量 {len(paper_ids)}，分析重点：{focus}。当前版本为接口预留实现，可替换为 LangGraph 工作流。"
