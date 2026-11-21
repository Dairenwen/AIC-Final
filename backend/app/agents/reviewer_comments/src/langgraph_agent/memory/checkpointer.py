"""Checkpointer 工厂：内存（测试）与 Postgres（生产）。

对齐 backend：
- 测试图流使用 InMemorySaver / MemorySaver
- 生产使用 PostgresSaver.from_conn_string(settings.libpq_url())
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver, MemorySaver

try:
    from langgraph.checkpoint.postgres import PostgresSaver
except ImportError:  # pragma: no cover - exercised on minimal Web installs
    class PostgresSaver:  # type: ignore[no-redef]
        """Lazy dependency placeholder for offline Web/SDK imports.

        The standalone reviewer package declares
        ``langgraph-checkpoint-postgres``.  The Web backend does not need a
        Postgres checkpointer for its deterministic facade, so importing the
        package must remain possible when that optional dependency is absent.
        Production checkpointer construction still raises an actionable error.
        """

        @classmethod
        def from_conn_string(cls, *_args: Any, **_kwargs: Any):
            raise RuntimeError(
                "审稿意见 Agent 缺少 langgraph-checkpoint-postgres，请安装 reviewer_comments/requirements.txt"
            )

from config.settings import Settings, get_settings


# MemorySaver 是 InMemorySaver 的别名；对外保留两者，便于文档与验收表述。
__all__ = [
    "InMemorySaver",
    "MemorySaver",
    "PostgresSaver",
    "Checkpointer",
    "CheckpointerContextFactory",
    "make_memory_checkpointer",
    "make_postgres_checkpointer",
    "make_postgres_checkpointer_cm_factory",
]


Checkpointer = Any
CheckpointerContextFactory = Callable[[], AbstractContextManager[PostgresSaver]]


def make_memory_checkpointer() -> InMemorySaver:
    """构造进程内 MemorySaver（即 InMemorySaver），供单测与离线演示。

    同一实例必须在 start / resume 之间复用，才能读到同一条 thread 的 checkpoint。
    """
    return InMemorySaver()


def make_postgres_checkpointer(
    libpq_url: str | None = None,
    *,
    pipeline: bool = False,
    settings: Settings | None = None,
) -> AbstractContextManager[PostgresSaver]:
    """返回 PostgresSaver 上下文管理器（对齐 backend Config.libpq_url 用法）。

    用法::

        with make_postgres_checkpointer() as checkpointer:
            graph = build_...(checkpointer=checkpointer, stores=stores)
            graph.invoke(...)

    Parameters
    ----------
    libpq_url:
        原生 libpq 连接串。为 None 时从 settings.libpq_url() 读取。
    pipeline:
        是否启用 psycopg pipeline（透传 PostgresSaver.from_conn_string）。
    settings:
        可选 Settings；仅在 libpq_url 为 None 时使用。
    """
    if libpq_url is None:
        cfg = settings if settings is not None else get_settings()
        libpq_url = cfg.libpq_url()
    return PostgresSaver.from_conn_string(libpq_url, pipeline=pipeline)


def make_postgres_checkpointer_cm_factory(
    libpq_url: str | None = None,
    *,
    pipeline: bool = False,
    settings: Settings | None = None,
) -> CheckpointerContextFactory:
    """构造可重复调用的 checkpointer 上下文工厂（对齐 backend *_checkpointer_cm_factory）。

    每次调用打开新连接；checkpoint 状态落在同一 PostgreSQL 实例上，跨调用可 resume。
    """

    @contextmanager
    def _factory() -> Iterator[PostgresSaver]:
        with make_postgres_checkpointer(
            libpq_url,
            pipeline=pipeline,
            settings=settings,
        ) as checkpointer:
            yield checkpointer

    return _factory
