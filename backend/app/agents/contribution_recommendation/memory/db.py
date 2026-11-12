"""数据库连接管理 — PostgreSQL / SQLite 双支持"""
import os
from typing import Optional
from sqlalchemy import create_engine, Engine, text
from sqlalchemy.orm import sessionmaker, Session
from utils.logger import get_logger

logger = get_logger(__name__)

# 默认连接配置
DEFAULT_PG_URL = "postgresql://submission_agent:submission_agent@localhost:5432/submission_agent_db"
DEFAULT_SQLITE_URL = "sqlite:///submission_agent.db"

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def get_db_url() -> str:
    """获取数据库连接 URL"""
    return os.getenv("SUBMISSION_DB_URL", DEFAULT_PG_URL)


def init_db(db_url: Optional[str] = None, use_sqlite: bool = False):
    """初始化数据库连接并创建表"""
    global _engine, _SessionLocal

    if use_sqlite:
        db_url = DEFAULT_SQLITE_URL
    else:
        db_url = db_url or get_db_url()

    logger.info(f"连接数据库: {db_url.split('@')[-1] if '@' in db_url else db_url}")

    connect_args = {}
    if "sqlite" in db_url:
        connect_args["check_same_thread"] = False

    try:
        _engine = create_engine(
            db_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
    except (ModuleNotFoundError, ImportError):
        # PostgreSQL 驱动未安装，自动降级 SQLite
        db_url = DEFAULT_SQLITE_URL
        logger.warning(f"PG 驱动不可用，降级 SQLite: {db_url}")
        connect_args = {"check_same_thread": False}
        _engine = create_engine(
            db_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            connect_args=connect_args,
        )

    # 导入模型并创建表
    from memory.models import Base
    Base.metadata.create_all(_engine)
    logger.info("数据库表已就绪")

    _SessionLocal = sessionmaker(bind=_engine)
    return _engine


def get_session() -> Session:
    """获取数据库会话"""
    if _SessionLocal is None:
        init_db()
    return _SessionLocal()


def check_connection() -> dict:
    """检查数据库连接状态"""
    try:
        session = get_session()
        session.execute(text("SELECT 1"))
        session.close()
        return {"status": "connected", "engine": str(_engine.url).split("@")[-1] if _engine and "@" in str(_engine.url) else "sqlite"}
    except Exception as e:
        return {"status": "disconnected", "error": str(e)}


def close_db():
    """关闭数据库连接"""
    global _engine
    if _engine:
        _engine.dispose()
        _engine = None
        logger.info("数据库连接已关闭")
