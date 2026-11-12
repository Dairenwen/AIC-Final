"""脚本与 Alembic 共用的轻量 import 引导。

问题：``langgraph_agent/__init__.py`` 会立刻导入 ReviewAgent/facade，
而 facade 又依赖 ``config.settings``；settings 又依赖
``langgraph_agent.utils.exceptions.ConfigError``，形成环。

本模块在导入 settings / models 之前，把包路径注入 sys.path，
并用「空壳 package + 按文件加载 exceptions」打断该环，
使 ``scripts/init_db.py`` 与 ``migrations/env.py`` 可不依赖 backend。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = _PACKAGE_ROOT / "src"
_PKG_DIR = _SRC_ROOT / "langgraph_agent"


def ensure_import_paths() -> Path:
    """将包根与 src/ 前置到 sys.path，返回包根 Path。"""
    for path in (str(_PACKAGE_ROOT), str(_SRC_ROOT)):
        if path not in sys.path:
            sys.path.insert(0, path)
    return _PACKAGE_ROOT


def _ensure_namespace(name: str, path: Path | None = None) -> types.ModuleType:
    """若模块尚未加载，注册为空壳 package（不执行其 __init__.py）。"""
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    module = types.ModuleType(name)
    module.__package__ = name
    if path is not None:
        module.__path__ = [str(path)]  # type: ignore[attr-defined]
        init_file = path / "__init__.py"
        if init_file.is_file():
            module.__file__ = str(init_file)
    sys.modules[name] = module
    return module


def _load_module_from_file(fullname: str, file_path: Path) -> types.ModuleType:
    """按文件路径加载模块并登记到 sys.modules（不经父包 __init__）。"""
    existing = sys.modules.get(fullname)
    if existing is not None and getattr(existing, "__file__", None):
        return existing
    spec = importlib.util.spec_from_file_location(fullname, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为 {fullname} 创建 spec：{file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[fullname] = module
    spec.loader.exec_module(module)
    return module


def bootstrap_langgraph_agent_light() -> None:
    """预置 langgraph_agent 命名空间并加载 exceptions，供 settings 使用。

    不执行 ``langgraph_agent/__init__.py``，避免拉起 facade 造成循环导入。
    之后可用常规 ``from config.settings import get_settings`` 等语句。
    """
    ensure_import_paths()
    _ensure_namespace("langgraph_agent", _PKG_DIR)
    _ensure_namespace("langgraph_agent.utils", _PKG_DIR / "utils")
    _load_module_from_file(
        "langgraph_agent.utils.exceptions",
        _PKG_DIR / "utils" / "exceptions.py",
    )


__all__ = [
    "bootstrap_langgraph_agent_light",
    "ensure_import_paths",
]
