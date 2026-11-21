"""Repository 公共会话与持久化辅助。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypeVar

from sqlalchemy.orm import Session, sessionmaker


ModelT = TypeVar("ModelT")


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """使用调用方注入的工厂管理一次显式事务。"""

    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def add_and_flush(session: Session, entity: ModelT) -> ModelT:
    """加入会话并 flush，使主键和数据库约束立即生效。"""

    session.add(entity)
    session.flush()
    return entity


def get_by_pk(
    session: Session,
    model: type[ModelT],
    primary_key: object,
) -> ModelT | None:
    """按 ORM 主键读取实体。"""

    return session.get(model, primary_key)
