"""Alembic 运行环境：使用 langgraph-agent 自身 Settings 与 ORM metadata。

不 import app.*；业务表元数据来自 adapters.postgres.models.Base。
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 将 langgraph-agent 根目录与 src/ 加入 import 路径，并打断包级循环导入
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _PACKAGE_ROOT / "scripts"
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _bootstrap import bootstrap_langgraph_agent_light  # noqa: E402

bootstrap_langgraph_agent_light()

from config.settings import get_settings  # noqa: E402
from langgraph_agent.adapters.postgres.models import Base  # noqa: E402

# Alembic Config 对象，可读 alembic.ini 中的值
config = context.config

# 用 Settings 中的数据库连接串覆盖 alembic.ini 里的占位值
_settings = get_settings()
if _settings.DATABASE_URL:
    config.set_main_option("sqlalchemy.url", _settings.sqlalchemy_url())

# 解释 ini 中的日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 业务表元数据（供 autogenerate 与 offline 模式使用）
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：仅用 URL 生成 SQL，不创建 Engine。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：创建 Engine 并在连接上执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
