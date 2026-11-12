"""一键初始化业务表与 LangGraph PostgreSQL Checkpointer 表。

在 langgraph-agent 包根目录执行::

    python scripts/init_db.py

依赖：.env 或环境变量中的 DATABASE_URL；不依赖 backend 路径。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _bootstrap import bootstrap_langgraph_agent_light  # noqa: E402

bootstrap_langgraph_agent_light()

from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402
from langgraph.checkpoint.postgres import PostgresSaver  # noqa: E402

from config.settings import get_settings  # noqa: E402
from langgraph_agent.utils.exceptions import ConfigError  # noqa: E402


ALEMBIC_INI = _PACKAGE_ROOT / "alembic.ini"


def main() -> int:
    """依次初始化业务表与 Checkpointer 表。

    本包当前仅使用 PostgresSaver，不初始化 PostgresStore；
    若后续引入 Store，再在此补充 setup。
    """
    try:
        settings = get_settings()
        settings.require_database()
    except ConfigError as error:
        print(f"[配置错误] {error}")
        return 1
    except Exception as error:  # noqa: BLE001
        print(f"[配置错误] {type(error).__name__}: {error}")
        return 1

    if not ALEMBIC_INI.is_file():
        print(f"[失败] 找不到 alembic.ini：{ALEMBIC_INI}")
        return 1

    try:
        alembic_config = AlembicConfig(str(ALEMBIC_INI))
        # 显式指定 script_location，避免 cwd 不是包根时找错路径
        alembic_config.set_main_option(
            "script_location", str(_PACKAGE_ROOT / "migrations")
        )
        command.upgrade(alembic_config, "head")
        print("[成功] 业务数据库迁移已升级到最新版本。")
    except Exception as error:  # noqa: BLE001
        print(f"[失败] 业务数据库迁移失败：{type(error).__name__}: {error}")
        return 1

    connection_url = settings.libpq_url()

    try:
        with PostgresSaver.from_conn_string(connection_url) as checkpointer:
            checkpointer.setup()
        print("[成功] Checkpointer（PostgresSaver）建表完成。")
    except Exception as error:  # noqa: BLE001
        print(f"[失败] Checkpointer 初始化失败：{type(error).__name__}: {error}")
        return 1

    # 本包 memory 层仅使用 PostgresSaver，未接入 PostgresStore，跳过 Store 建表。
    print("[跳过] 本包未使用 PostgresStore，不初始化 Store 表。")

    print("\n数据库初始化全部完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
