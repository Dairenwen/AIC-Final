"""Initialize an empty PostgreSQL database without starting HTTP/model services."""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv
load_dotenv(BACKEND_ROOT / ".env")

from flask import Flask
from sqlalchemy import select, text
from werkzeug.security import generate_password_hash

from app.config import Config
from app.extensions import db
from app import model_registry  # noqa: F401
from app.models import Agent, ModelProvider, ModelType, Role, Tool, User
from app.services import catalog_setup as catalog


def bootstrap_app() -> Flask:
    app = Flask("zhiyan-bootstrap")
    app.config.from_object(Config)
    db.init_app(app)
    return app


def initialize_database(app: Flask, *, knowledge_base: bool = True) -> None:
    with app.app_context():
        if db.engine.dialect.name != "postgresql":
            raise RuntimeError("此初始化入口仅支持 PostgreSQL")
        with db.engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(20261008, 1)"))
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS zhiyan"))
            db.metadata.create_all(bind=connection)
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS zhiyan.system_settings (
                    setting_key VARCHAR(100) PRIMARY KEY,
                    value_json JSONB NOT NULL,
                    description TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """))
        # Insert missing catalog rows only; retain restored/customized data.
        for code, name in (
            ("normal_user", "普通用户"), ("system_admin", "系统管理员"),
        ):
            if db.session.get(Role, code) is None:
                db.session.add(Role(code=code, name=name))
        for code, name in (("chat", "对话模型"), ("embedding", "向量模型"), ("rerank", "重排模型")):
            if db.session.get(ModelType, code) is None:
                db.session.add(ModelType(code=code, name=name))
        for code, name, capabilities in (
            ("openai_compatible", "OpenAI Compatible", ["chat_completions"]),
            ("deepseek", "DeepSeek", ["chat_completions"]),
            ("dashscope", "通义千问", ["chat_completions"]),
        ):
            if db.session.get(ModelProvider, code) is None:
                db.session.add(ModelProvider(code=code, name=name, allow_custom_url=True, capabilities=capabilities))
        definitions = [
            value for name, value in vars(catalog).items()
            if name.endswith("_AGENT") and isinstance(value, dict) and "code" in value
        ]
        # These two agents already exist in the application, but had no seed entry.
        definitions.extend([
            dict(code="literature_search", name="文献检索", category="学术检索", version=2,
                 description="六阶段 LangGraph 工作流：查询改写、四路检索、去重排序、报告生成、文献列表和年度脉络图。",
                 config_json={"runtime": "builtin", "route": "/agents/literature-search",
                              "capabilities": ["query_rewrite", "parallel_retrieval", "ranking", "report", "timeline"]}),
            dict(code="manuscript_assistance", name="文稿辅助", category="论文写作", version=1,
                 description="面向科研论文的章节生成、内容润色、引用辅助和结构一致性检查。",
                 config_json={"runtime": "builtin", "route": "/agents/manuscript-assistance",
                              "capabilities": ["outline", "section_writing", "polishing", "citation", "quality_check"]}),
        ])
        for definition in definitions:
            if db.session.scalar(select(Agent).where(Agent.code == definition["code"])) is None:
                db.session.add(Agent(**definition))
        for definition in catalog.BUILTIN_RESEARCH_TOOLS:
            if db.session.scalar(select(Tool).where(Tool.code == definition["code"])) is None:
                db.session.add(Tool(**definition))
        db.session.commit()
        if knowledge_base:
            from app.integrations.knowledge_base.runtime import _configure_runtime
            _configure_runtime(app)
            from knowledge_base_runtime.backend.dao.database import init_db
            init_db()


def create_admin(app: Flask, phone: str, password: str) -> None:
    from app.api.auth import normalize_phone
    normalized = normalize_phone(phone)
    if normalized is None or len(password) < 12:
        raise ValueError("手机号格式不正确，或管理员密码不足 12 个字符")
    with app.app_context():
        if db.session.scalar(select(User).where(User.phone == normalized)) is not None:
            raise ValueError("该手机号已存在；不会修改其密码或权限")
        db.session.add(User(phone=normalized, display_name="系统管理员", role_code="system_admin",
                            password_hash=generate_password_hash(password)))
        db.session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-knowledge-base", action="store_true")
    parser.add_argument("--admin-phone", help="可选：创建管理员，密码交互输入，不写入命令行")
    args = parser.parse_args()
    app = bootstrap_app()
    initialize_database(app, knowledge_base=not args.skip_knowledge_base)
    if args.admin_phone:
        password = getpass.getpass("管理员密码（至少12字符）：")
        if password != getpass.getpass("确认密码："):
            raise ValueError("两次密码不一致")
        create_admin(app, args.admin_phone, password)
    print("数据库初始化完成；现有数据保留，未启动模型、短信或 HTTP 服务。")


if __name__ == "__main__":
    main()
