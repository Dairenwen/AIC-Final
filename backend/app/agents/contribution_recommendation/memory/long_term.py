"""
长期记忆 — PostgreSQL 持久化存储

功能：
- 对话历史持久化
- 用户偏好持久化 + 自动学习
- 推荐历史记录
- 用户反馈收集
- 用户画像洞察
"""

from typing import Optional
from datetime import datetime

from memory.db import get_session
from memory.models import (
    ConversationTurn, UserPreference, RecommendationRecord,
    UserFeedback, UserInsight,
)
from utils.json_helper import to_json
from utils.logger import get_logger

logger = get_logger(__name__)


class LongTermMemory:
    """长期记忆管理器 — PostgreSQL 后端"""

    # ── 对话历史 ──────────────────────────────────

    def save_turn(self, session_id: str, role: str, content: str,
                  user_id: str = "default", metadata: Optional[dict] = None,
                  token_count: int = 0):
        """保存一轮对话"""
        session = get_session()
        try:
            turn = ConversationTurn(
                session_id=session_id,
                user_id=user_id,
                role=role,
                content=content,
                metadata_json=metadata or {},
                token_count=token_count,
            )
            session.add(turn)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"保存对话失败: {e}")
        finally:
            session.close()

    def load_history(self, session_id: str, limit: int = 50) -> list[dict]:
        """加载对话历史"""
        session = get_session()
        try:
            turns = (
                session.query(ConversationTurn)
                .filter(ConversationTurn.session_id == session_id)
                .order_by(ConversationTurn.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {"role": t.role, "content": t.content,
                 "timestamp": t.created_at.isoformat() if t.created_at else "",
                 "metadata": t.metadata_json}
                for t in reversed(turns)
            ]
        finally:
            session.close()

    def load_user_history(self, user_id: str, limit: int = 100) -> list[dict]:
        """加载用户所有历史对话"""
        session = get_session()
        try:
            turns = (
                session.query(ConversationTurn)
                .filter(ConversationTurn.user_id == user_id)
                .order_by(ConversationTurn.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {"session_id": t.session_id, "role": t.role,
                 "content": t.content[:200],
                 "timestamp": t.created_at.isoformat() if t.created_at else ""}
                for t in turns
            ]
        finally:
            session.close()

    # ── 用户偏好 ──────────────────────────────────

    def save_preferences(self, user_id: str, preferences: dict):
        """保存/更新用户偏好"""
        session = get_session()
        try:
            existing = (
                session.query(UserPreference)
                .filter(UserPreference.user_id == user_id)
                .first()
            )
            if existing:
                existing.preferences = preferences
                existing.version += 1
                existing.updated_at = datetime.utcnow()
            else:
                pref = UserPreference(user_id=user_id, preferences=preferences)
                session.add(pref)
            session.commit()
            logger.info(f"用户偏好已保存: {user_id} (版本 {existing.version if existing else 1})")
        except Exception as e:
            session.rollback()
            logger.error(f"保存偏好失败: {e}")
        finally:
            session.close()

    def load_preferences(self, user_id: str) -> dict:
        """加载用户偏好"""
        session = get_session()
        try:
            pref = (
                session.query(UserPreference)
                .filter(UserPreference.user_id == user_id)
                .first()
            )
            return pref.preferences if pref else {}
        finally:
            session.close()

    # ── 推荐历史 ──────────────────────────────────

    def save_recommendation(self, task_id: str, user_id: str,
                            paper_id: str, paper_title: str,
                            paper_abstract: str = "",
                            paper_features: Optional[dict] = None,
                            quality_estimate: Optional[dict] = None,
                            preferences_snapshot: Optional[dict] = None,
                            recommendations: Optional[list] = None,
                            final_report: str = "",
                            execution_time_ms: int = 0,
                            model_used: str = ""):
        """保存推荐记录"""
        session = get_session()
        try:
            recs = recommendations or []
            record = RecommendationRecord(
                task_id=task_id,
                user_id=user_id,
                paper_id=paper_id,
                paper_title=paper_title,
                paper_abstract=paper_abstract[:2000],
                paper_features=paper_features,
                quality_estimate=quality_estimate,
                preferences_snapshot=preferences_snapshot,
                recommendations=recs,
                top_venue=recs[0]["venue"]["abbreviation"] if recs else "",
                top_score=recs[0]["match_score"].get("overall", 0) if recs else 0,
                tier_distribution={
                    "sprint": sum(1 for r in recs if r.get("tier") == "sprint"),
                    "match": sum(1 for r in recs if r.get("tier") == "match"),
                    "safety": sum(1 for r in recs if r.get("tier") == "safety"),
                },
                final_report=final_report[:10000],
                execution_time_ms=execution_time_ms,
                model_used=model_used,
            )
            session.add(record)
            session.commit()
            logger.info(f"推荐记录已保存: {task_id}")
        except Exception as e:
            session.rollback()
            logger.error(f"保存推荐记录失败: {e}")
        finally:
            session.close()

    def load_recommendation(self, task_id: str) -> Optional[dict]:
        """加载推荐记录"""
        session = get_session()
        try:
            rec = (
                session.query(RecommendationRecord)
                .filter(RecommendationRecord.task_id == task_id)
                .first()
            )
            if not rec:
                return None
            return {
                "task_id": rec.task_id,
                "user_id": rec.user_id,
                "paper_id": rec.paper_id,
                "paper_title": rec.paper_title,
                "recommendations": rec.recommendations,
                "top_venue": rec.top_venue,
                "top_score": rec.top_score,
                "final_report": rec.final_report,
                "created_at": rec.created_at.isoformat() if rec.created_at else "",
            }
        finally:
            session.close()

    def load_user_recommendations(self, user_id: str, limit: int = 20) -> list[dict]:
        """加载用户推荐历史"""
        session = get_session()
        try:
            recs = (
                session.query(RecommendationRecord)
                .filter(RecommendationRecord.user_id == user_id)
                .order_by(RecommendationRecord.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "task_id": r.task_id,
                    "paper_title": r.paper_title,
                    "top_venue": r.top_venue,
                    "top_score": r.top_score,
                    "created_at": r.created_at.isoformat() if r.created_at else "",
                }
                for r in recs
            ]
        finally:
            session.close()

    # ── 用户反馈 ──────────────────────────────────

    def save_feedback(self, task_id: str, user_id: str, rating: int = 0,
                      accepted_recommendation: str = "",
                      actual_submission_venue: str = "",
                      actual_result: str = "",
                      comments: str = ""):
        """保存用户反馈"""
        session = get_session()
        try:
            feedback = UserFeedback(
                task_id=task_id,
                user_id=user_id,
                rating=rating,
                accepted_recommendation=accepted_recommendation,
                actual_submission_venue=actual_submission_venue,
                actual_result=actual_result,
                comments=comments,
            )
            session.add(feedback)
            session.commit()
            logger.info(f"反馈已保存: {task_id}")
            # 反馈后更新用户画像
            self._update_insights(user_id)
        except Exception as e:
            session.rollback()
            logger.error(f"保存反馈失败: {e}")
        finally:
            session.close()

    def load_feedback(self, task_id: str) -> Optional[dict]:
        """加载反馈"""
        session = get_session()
        try:
            fb = (
                session.query(UserFeedback)
                .filter(UserFeedback.task_id == task_id)
                .first()
            )
            if not fb:
                return None
            return {
                "task_id": fb.task_id,
                "rating": fb.rating,
                "accepted_recommendation": fb.accepted_recommendation,
                "actual_submission_venue": fb.actual_submission_venue,
                "actual_result": fb.actual_result,
                "comments": fb.comments,
            }
        finally:
            session.close()

    # ── 用户画像 ──────────────────────────────────

    def _update_insights(self, user_id: str):
        """根据历史数据自动更新用户画像"""
        session = get_session()
        try:
            # 分析推荐历史中的偏好
            recs = (
                session.query(RecommendationRecord)
                .filter(RecommendationRecord.user_id == user_id)
                .all()
            )
            if not recs:
                return

            # 统计最常研究领域
            from collections import Counter
            field_counter = Counter()
            level_counter = Counter()
            for r in recs:
                features = r.paper_features or {}
                for f in features.get("sub_fields", []):
                    field_counter[f] += 1
                prefs = r.preferences_snapshot or {}
                for lv in prefs.get("target_ccf_levels", []):
                    level_counter[lv] += 1

            # 保存洞察
            if field_counter:
                insight = UserInsight(
                    user_id=user_id,
                    insight_type="preferred_field",
                    insight_key="top_fields",
                    insight_value=dict(field_counter.most_common(5)),
                    confidence=min(len(recs) / 10, 0.9),
                    source_count=len(recs),
                )
                # upsert
                existing = (
                    session.query(UserInsight)
                    .filter(UserInsight.user_id == user_id,
                            UserInsight.insight_key == "top_fields")
                    .first()
                )
                if existing:
                    existing.insight_value = insight.insight_value
                    existing.confidence = insight.confidence
                    existing.source_count = insight.source_count
                else:
                    session.add(insight)

            session.commit()
        except Exception as e:
            session.rollback()
            logger.warning(f"更新用户画像失败: {e}")
        finally:
            session.close()

    def load_insights(self, user_id: str) -> list[dict]:
        """加载用户画像"""
        session = get_session()
        try:
            insights = (
                session.query(UserInsight)
                .filter(UserInsight.user_id == user_id)
                .all()
            )
            return [
                {
                    "type": i.insight_type,
                    "key": i.insight_key,
                    "value": i.insight_value,
                    "confidence": i.confidence,
                    "source_count": i.source_count,
                }
                for i in insights
            ]
        finally:
            session.close()

    # ── 反馈驱动的推荐权重调整 ──────────────────────

    def compute_feedback_adjustments(self, user_id: str) -> dict:
        """
        根据用户历史反馈，计算每个 venue 的个性化权值调整。

        逻辑:
          - 曾经录用的 venue → +0.05 boost (同类 venue 也受益)
          - 曾经被拒的 venue → -0.03 penalty
          - 从未投过的 venue → 0 (不影响)
          - 数据越多，调整幅度越大

        Returns:
            {venue_abbr: adjustment_score, ...}
        """
        session = get_session()
        try:
            feedbacks = (
                session.query(UserFeedback)
                .filter(UserFeedback.user_id == user_id)
                .all()
            )
            if not feedbacks:
                return {}

            adjustments = {}
            for fb in feedbacks:
                venue = fb.actual_submission_venue or fb.accepted_recommendation
                if not venue:
                    continue
                result = fb.actual_result or ""
                if result.lower() in ("accepted", "录用"):
                    adjustments[venue.upper()] = adjustments.get(venue.upper(), 0) + 0.05
                elif result.lower() in ("rejected", "拒稿"):
                    adjustments[venue.upper()] = adjustments.get(venue.upper(), 0) - 0.03

            # 同类 venue 也小幅受益（同 CCF 级别）
            if adjustments:
                from knowledge import get_knowledge_base
                kb = get_knowledge_base()
                bonus_venues = {v for v, s in adjustments.items() if s > 0}
                for bonus_v in bonus_venues:
                    bonus_venue = kb.get_by_id(bonus_v)
                    if not bonus_venue:
                        continue
                    bonus_level = bonus_venue.get("ccf_level", "")
                    bonus_areas = set(a.lower() for a in bonus_venue.get("research_areas", []))
                    for v in kb.get_all():
                        if v["abbreviation"] == bonus_v:
                            continue
                        if v.get("ccf_level") == bonus_level:
                            v_areas = set(a.lower() for a in v.get("research_areas", []))
                            overlap = len(bonus_areas & v_areas)
                            if overlap > 0:
                                adj = 0.01 * overlap
                                adjustments[v["abbreviation"]] = (
                                    adjustments.get(v["abbreviation"], 0) + adj
                                )

            logger.info(f"反馈权重调整: {len(adjustments)} 个 venue 受影响 (user={user_id})")
            return adjustments
        except Exception as e:
            logger.warning(f"计算权重调整失败: {e}")
            return {}
        finally:
            session.close()


# 全局单例
_long_term: Optional[LongTermMemory] = None


def get_long_term_memory() -> LongTermMemory:
    global _long_term
    if _long_term is None:
        _long_term = LongTermMemory()
    return _long_term
