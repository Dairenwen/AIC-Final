"""数据库 ORM 模型 — SQLAlchemy + PostgreSQL"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, JSON, Index, create_engine
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class ConversationTurn(Base):
    """短期记忆：对话轮次"""
    __tablename__ = "conversation_turns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, index=True)
    user_id = Column(String(64), default="default")
    role = Column(String(16), nullable=False)  # user / assistant / system
    content = Column(Text, nullable=False)
    metadata_json = Column(JSON, default={})
    token_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_session_created", "session_id", "created_at"),
    )


class UserPreference(Base):
    """长期记忆：用户偏好"""
    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), unique=True, nullable=False, index=True)
    preferences = Column(JSON, nullable=False, default={})
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RecommendationRecord(Base):
    """长期记忆：推荐历史"""
    __tablename__ = "recommendation_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    paper_id = Column(String(64))
    paper_title = Column(Text)
    paper_abstract = Column(Text)
    paper_features = Column(JSON)
    quality_estimate = Column(JSON)
    preferences_snapshot = Column(JSON)
    recommendations = Column(JSON)
    top_venue = Column(String(128))
    top_score = Column(Float)
    tier_distribution = Column(JSON)
    final_report = Column(Text)
    execution_time_ms = Column(Integer)
    model_used = Column(String(64))
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class UserFeedback(Base):
    """长期记忆：用户反馈"""
    __tablename__ = "user_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), nullable=False, index=True)
    user_id = Column(String(64), nullable=False)
    rating = Column(Integer)  # 1-5
    accepted_recommendation = Column(String(128))
    actual_submission_venue = Column(String(128))
    actual_result = Column(String(64))  # accepted / rejected / under_review
    comments = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserInsight(Base):
    """长期记忆：用户画像洞察"""
    __tablename__ = "user_insights"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    insight_type = Column(String(32))  # preferred_field / preferred_level / pattern
    insight_key = Column(String(128))
    insight_value = Column(JSON)
    confidence = Column(Float, default=0.5)
    source_count = Column(Integer, default=1)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
