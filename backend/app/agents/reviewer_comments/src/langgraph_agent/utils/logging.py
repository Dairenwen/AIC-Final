"""包内统一日志入口。"""

from __future__ import annotations

import logging

_PACKAGE_ROOT = "langgraph_agent"


def get_logger(name: str | None = None) -> logging.Logger:
    """返回包内统一命名的 logger。

    - ``None`` → 根 logger ``langgraph_agent``
    - 已以 ``langgraph_agent`` / ``config`` 开头 → 原样使用
    - 其他相对名 → 自动加 ``langgraph_agent.`` 前缀
    """
    if not name:
        return logging.getLogger(_PACKAGE_ROOT)
    if (
        name == _PACKAGE_ROOT
        or name.startswith(f"{_PACKAGE_ROOT}.")
        or name == "config"
        or name.startswith("config.")
    ):
        return logging.getLogger(name)
    return logging.getLogger(f"{_PACKAGE_ROOT}.{name}")
