"""迁移配置烟测：不连接真实数据库。"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = PACKAGE_ROOT / "alembic.ini"
MIGRATIONS_DIR = PACKAGE_ROOT / "migrations"
VERSIONS_DIR = MIGRATIONS_DIR / "versions"

# 与 backend 对齐的 revision 链（保持 id 一致，便于共用库）
EXPECTED_REVISIONS = (
    "202607210001",
    "202607210002",
    "202607230003",
)


def test_alembic_ini_exists_and_loads() -> None:
    """alembic.ini 存在且可被 Alembic 加载。"""
    assert ALEMBIC_INI.is_file(), f"缺少 alembic.ini：{ALEMBIC_INI}"
    config = AlembicConfig(str(ALEMBIC_INI))
    script_location = config.get_main_option("script_location")
    assert script_location, "alembic.ini 未配置 script_location"
    # 解析后应能定位到本包 migrations
    resolved = Path(config.get_main_option("script_location") or "")
    # %(here)s 展开后为绝对路径或相对包根
    assert "migrations" in str(script_location).replace("\\", "/")


def test_migrations_versions_directory_exists() -> None:
    """versions 目录存在且包含 3 个 revision 脚本。"""
    assert VERSIONS_DIR.is_dir(), f"缺少 versions 目录：{VERSIONS_DIR}"
    version_files = sorted(
        p.name for p in VERSIONS_DIR.glob("*.py") if p.name != "__init__.py"
    )
    assert len(version_files) >= 3, f"version 文件不足：{version_files}"
    for expected in (
        "20260721_0001_create_business_tables.py",
        "20260721_0002_create_paper_cards.py",
        "20260723_0003_add_source_expression_settings_override.py",
    ):
        assert expected in version_files, f"缺少迁移文件：{expected}"


def test_alembic_script_directory_discovers_revisions() -> None:
    """Alembic ScriptDirectory 能发现完整 revision 链。"""
    config = AlembicConfig(str(ALEMBIC_INI))
    # 强制 script_location 为绝对路径，避免 cwd 干扰
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    script = ScriptDirectory.from_config(config)
    revisions = list(script.walk_revisions())
    rev_ids = [item.revision for item in revisions]
    for expected in EXPECTED_REVISIONS:
        assert expected in rev_ids, f"未发现 revision {expected}，当前：{rev_ids}"
    # head 应为最新 0003
    heads = script.get_heads()
    assert "202607230003" in heads, f"head 异常：{heads}"


def test_version_files_have_no_backend_app_imports() -> None:
    """迁移脚本不得依赖 backend 的 app.* 导入。"""
    pattern = re.compile(r"^\s*(from|import)\s+app(\.|$|\s)", re.MULTILINE)
    offenders: list[str] = []
    for path in VERSIONS_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(path.name)
    assert not offenders, f"迁移仍引用 app.*：{offenders}"


def test_env_module_source_has_no_app_imports() -> None:
    """env.py 不得出现可执行的 app.* 导入；使用本包 Settings 与 models。"""
    env_path = MIGRATIONS_DIR / "env.py"
    assert env_path.is_file()
    text = env_path.read_text(encoding="utf-8")
    app_import = re.compile(r"^\s*(from|import)\s+app(\.|$|\s)", re.MULTILINE)
    assert not app_import.search(text), "env.py 仍含 app.* 导入语句"
    assert "config.settings" in text or "get_settings" in text
    assert "langgraph_agent.adapters.postgres.models" in text
    assert "target_metadata" in text


def test_env_py_is_loadable_as_source() -> None:
    """env.py 可作为源文件被解析（不执行 run_migrations，避免连库）。

    直接 import migrations.env 会触发 Alembic context 与 get_settings，
    在无 DATABASE_URL / 非 alembic 进程中可能副作用较大；
    这里只验证文件语法与关键符号存在。
    """
    env_path = MIGRATIONS_DIR / "env.py"
    source = env_path.read_text(encoding="utf-8")
    # 编译检查语法
    compile(source, str(env_path), "exec")
    # 关键入口存在
    assert "def run_migrations_offline" in source
    assert "def run_migrations_online" in source


def test_init_db_script_exists_and_is_valid_python() -> None:
    """scripts/init_db.py 存在且语法合法、不依赖 backend 路径。"""
    init_db = PACKAGE_ROOT / "scripts" / "init_db.py"
    assert init_db.is_file(), f"缺少 init_db.py：{init_db}"
    source = init_db.read_text(encoding="utf-8")
    compile(source, str(init_db), "exec")
    assert "backend" not in source.lower() or "不依赖 backend" in source
    # 不应硬编码 backend 路径
    assert "backend/alembic" not in source
    assert "app.config" not in source
    assert "PostgresSaver" in source
    assert "command.upgrade" in source


def test_bootstrap_module_breaks_circular_import() -> None:
    """_bootstrap 可在不加载 facade 的前提下导入 Settings。"""
    import importlib
    import sys

    def _is_target(name: str) -> bool:
        return (
            name == "config"
            or name.startswith("config.")
            or name == "langgraph_agent"
            or name.startswith("langgraph_agent.")
        )

    # 快照被本测清理的模块，测试结束后恢复，避免污染后续测试的
    # monkeypatch 目标（例如 tools.paper_card.get_settings 被 patch 后失效）。
    saved_modules = {
        name: module for name, module in sys.modules.items() if _is_target(name)
    }

    # 清理可能残留的部分初始化模块，保证本测独立
    for name in list(sys.modules):
        if _is_target(name):
            del sys.modules[name]

    try:
        scripts_dir = str(PACKAGE_ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        if str(PACKAGE_ROOT) not in sys.path:
            sys.path.insert(0, str(PACKAGE_ROOT))

        bootstrap = importlib.import_module("_bootstrap")
        bootstrap.bootstrap_langgraph_agent_light()

        from config.settings import Settings, get_settings

        assert Settings is not None
        settings = get_settings()
        assert hasattr(settings, "DATABASE_URL")
        assert hasattr(settings, "sqlalchemy_url")
        assert hasattr(settings, "libpq_url")

        from langgraph_agent.adapters.postgres.models import Base

        assert "workspaces" in Base.metadata.tables
        # 确认未通过 bootstrap 拉起 facade
        assert "langgraph_agent.agent.facade" not in sys.modules
    finally:
        # 移除本测导入的新模块副本，恢复快照，确保后续测试拿回原始模块对象
        for name in list(sys.modules):
            if _is_target(name):
                del sys.modules[name]
        sys.modules.update(saved_modules)


def test_paper_cards_migration_imports_local_schemas() -> None:
    """0002 迁移从本包 paper_schemas 取枚举，而非 app.parsing。"""
    path = VERSIONS_DIR / "20260721_0002_create_paper_cards.py"
    text = path.read_text(encoding="utf-8")
    assert "langgraph_agent.tools.paper_schemas" in text
    assert "app.parsing" not in text
