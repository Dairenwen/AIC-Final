"""日志配置"""

import logging
import sys
from ..config import config


def get_logger(name: str) -> logging.Logger:
    """获取配置好的 Logger 实例

    Args:
        name: 日志器名称（通常使用 __name__）

    Returns:
        配置好的 Logger
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    log_level = getattr(logging, config.agent.verbose and "DEBUG" or "INFO")
    logger.setLevel(log_level)

    return logger
