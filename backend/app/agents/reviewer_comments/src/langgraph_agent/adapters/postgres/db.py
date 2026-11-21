"""数据库引擎与 Session 工厂。

对齐 backend/app/models/__init__.py 的 get_engine / create_session_factory；
连接串取自 config.settings.Settings，不引入 Flask。
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import Settings, get_settings


def get_engine(settings: Settings | None = None) -> Engine:
    """创建数据库引擎，使用 postgresql+psycopg:// 连接串。

    开启 pool_pre_ping：长时间运行后先探活再复用连接。
    """
    cfg = settings if settings is not None else get_settings()
    return create_engine(
        cfg.sqlalchemy_url(),
        future=True,
        pool_pre_ping=True,
    )


def create_session_factory(
    engine: Engine | None = None,
    settings: Settings | None = None,
) -> sessionmaker[Session]:
    """创建 Session 工厂；未传入引擎时按配置新建。"""
    if engine is None:
        engine = get_engine(settings)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
