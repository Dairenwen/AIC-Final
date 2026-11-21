from __future__ import annotations

import json
import os
from uuid import uuid4

import psycopg


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://zhiyan:zhiyan@127.0.0.1:5432/zhiyan")

AGENTS = [
    {
        "code": "literature_search",
        "name": "文献检索",
        "category": "学术检索",
        "description": "六阶段 LangGraph 工作流：查询改写、四路检索、去重排序、报告生成、文献列表和年度脉络图。",
        "version": 2,
        "config_json": {
            "runtime": "builtin",
            "route": "/agents/literature-search",
            "capabilities": ["query_rewrite", "parallel_retrieval", "ranking", "report", "timeline"],
        },
    },
    {
        "code": "manuscript_assistance",
        "name": "文稿辅助",
        "category": "论文写作",
        "description": "面向科研论文的章节生成、内容润色、引用辅助和结构一致性检查。",
        "version": 1,
        "config_json": {
            "runtime": "builtin",
            "route": "/agents/manuscript-assistance",
            "capabilities": ["outline", "section_writing", "polishing", "citation", "quality_check"],
        },
    },
    {
        "code": "innovation_point_generation",
        "name": "创新点生成",
        "category": "科研选题",
        "description": "结合本地文献语料完成趋势分析、研究空白识别、创新点生成、评分评估和证据绑定。",
        "version": 1,
        "config_json": {
            "runtime": "builtin",
            "route": "/agents/innovation-point-generation",
            "capabilities": ["trend_analysis", "gap_identification", "idea_generation", "evaluation", "evidence_binding"],
        },
    },
]


def main() -> None:
    uri = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(uri) as connection:
        connection.execute(
            """
            INSERT INTO zhiyan.users (
                id, phone, password_hash, display_name, role_code, status,
                phone_verified_at, profile
            ) VALUES (
                %s, %s, %s, %s, 'normal_user', 'ACTIVE', now(), %s::jsonb
            )
            ON CONFLICT (phone) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                profile = EXCLUDED.profile,
                updated_at = now()
            """,
            (
                uuid4(),
                "+8613800000000",
                "LOGIN_NOT_CONFIGURED",
                "本地科研用户",
                json.dumps({"organization": "武汉理工大学", "plan": "科研基础版", "bootstrap": True}, ensure_ascii=False),
            ),
        )
        for agent in AGENTS:
            connection.execute(
                """
                INSERT INTO zhiyan.agents (
                    id, code, name, category, description, version, config_json, status
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s::jsonb, 'ACTIVE'
                )
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    category = EXCLUDED.category,
                    description = EXCLUDED.description,
                    version = EXCLUDED.version,
                    config_json = EXCLUDED.config_json,
                    status = EXCLUDED.status,
                    updated_at = now()
                """,
                (
                    uuid4(),
                    agent["code"],
                    agent["name"],
                    agent["category"],
                    agent["description"],
                    agent["version"],
                    json.dumps(agent["config_json"], ensure_ascii=False),
                ),
            )
        connection.commit()


if __name__ == "__main__":
    main()
