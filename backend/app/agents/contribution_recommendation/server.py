"""
投稿推荐 Agent — FastAPI REST API Server

启动: uvicorn server:app --host 0.0.0.0 --port 8000 --reload
文档: http://localhost:8000/docs
"""

import sys, os, io, time, uuid, asyncio
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path
from datetime import datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent import recommend_submission, SubmissionRecommendAgent
from config import Config, UserPreferences
from memory import (
    init_db, check_connection,
    get_short_term_memory, get_long_term_memory,
)

# ── FastAPI 应用 ──────────────────────────────────

app = FastAPI(
    title="智研 · 投稿推荐 Agent API",
    description="基于 LangGraph + LangChain 的论文投稿推荐系统",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 请求/响应模型 ────────────────────────────────

class PaperInput(BaseModel):
    title: str = Field(..., description="论文标题")
    abstract: str = Field(default="", description="摘要")
    keywords: list[str] = Field(default=[], description="关键词")
    references: list[dict] = Field(default=[], description="参考文献 [{title, venue, year}]")

class QualityInput(BaseModel):
    experiment_completeness: float = Field(default=0.7, ge=0, le=1.0, description="实验完整度 0-1")
    novelty_level: str = Field(default="substantial", description="incremental / substantial / breakthrough")
    theoretical_rigor: float = Field(default=0.7, ge=0, le=1.0)
    writing_quality: float = Field(default=0.7, ge=0, le=1.0)

class PreferenceInput(BaseModel):
    target_ccf_levels: list[str] = Field(default=["CCF-A", "CCF-B"])
    max_review_weeks: int = Field(default=16, description="审稿周期上限(周)")
    prefer_oa: bool = Field(default=False)
    max_publication_fee: float = Field(default=0)
    excluded_venues: list[str] = Field(default=[])
    sprint_tier_count: int = Field(default=3)
    match_tier_count: int = Field(default=5)
    safety_tier_count: int = Field(default=3)

class RecommendRequest(BaseModel):
    paper: PaperInput
    quality: QualityInput = Field(default_factory=QualityInput)
    preferences: PreferenceInput = Field(default_factory=PreferenceInput)
    user_id: str = Field(default="default")
    session_id: Optional[str] = Field(default=None)

class RerankRequest(BaseModel):
    task_id: str
    preferences: PreferenceInput
    user_id: str = Field(default="default")

class FeedbackRequest(BaseModel):
    task_id: str
    user_id: str = Field(default="default")
    rating: int = Field(default=0, ge=0, le=5)
    accepted_recommendation: str = Field(default="")
    actual_submission_venue: str = Field(default="")
    actual_result: str = Field(default="")
    comments: str = Field(default="")


# ── 启动事件 ─────────────────────────────────────

@app.on_event("startup")
async def startup():
    """初始化数据库连接（无 PG 时降级为 SQLite）"""
    try:
        init_db()
        status = check_connection()
        print(f"数据库: {status['status']} ({status.get('engine', '')})")
    except Exception:
        print("PostgreSQL 不可用，使用 SQLite")
        init_db(use_sqlite=True)


# ══════════════════════════════════════════════════
# 核心 API
# ══════════════════════════════════════════════════

@app.post("/api/v1/recommend")
async def api_recommend(req: RecommendRequest):
    """
    ## 提交投稿推荐任务

    输入论文信息 + 质量估计 + 用户偏好，返回完整推荐结果。

    ### 示例请求:
    ```json
    {
      "paper": {
        "title": "Dynamic Routing for Cross-Modal Transfer",
        "abstract": "We propose a novel...",
        "keywords": ["transfer learning", "cross-modal"],
        "references": [{"title": "...", "venue": "NeurIPS", "year": 2024}]
      },
      "quality": {
        "experiment_completeness": 0.72,
        "novelty_level": "substantial"
      },
      "preferences": {
        "target_ccf_levels": ["CCF-A", "CCF-B"],
        "max_review_weeks": 12
      },
      "user_id": "researcher_001"
    }
    ```
    """
    start_time = time.time()
    short_mem = get_short_term_memory()
    long_mem = get_long_term_memory()

    session_id = req.session_id or f"SESS-{uuid.uuid4().hex[:8]}"
    task_id = f"SR-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"

    # 短期记忆：记录用户请求
    short_mem.add_turn(session_id, "user",
                       f"投稿推荐: {req.paper.title}", {"task_id": task_id})

    # 长期记忆：加载用户历史偏好（合并）
    saved_prefs = long_mem.load_preferences(req.user_id)
    merged_prefs = {**req.preferences.model_dump()}
    if saved_prefs:
        # 历史偏好加权合并
        for k in ["target_ccf_levels", "max_review_weeks", "prefer_oa"]:
            if k not in merged_prefs or not merged_prefs[k]:
                merged_prefs[k] = saved_prefs.get(k, merged_prefs.get(k))

    # 执行推荐
    result = await recommend_submission(
        paper_id=f"API-{task_id}",
        parsed_paper={
            "title": req.paper.title,
            "abstract": req.paper.abstract,
            "keywords": req.paper.keywords,
            "references": req.paper.references,
        },
        quality_estimate={
            "experiment_completeness": req.quality.experiment_completeness,
            "novelty_level": req.quality.novelty_level,
            "theoretical_rigor": req.quality.theoretical_rigor,
            "writing_quality": req.quality.writing_quality,
        },
        user_preferences=merged_prefs,
    )

    execution_ms = int((time.time() - start_time) * 1000)

    # 短期记忆：记录推荐结果
    top_rec = result.get("recommendations", [{}])[0] if result.get("recommendations") else {}
    short_mem.add_turn(session_id, "assistant",
                       f"推荐: {top_rec.get('venue', {}).get('abbreviation', 'N/A')} "
                       f"({top_rec.get('tier', 'N/A')})",
                       {"task_id": task_id})

    # 长期记忆：持久化
    long_mem.save_turn(session_id, "user",
                       f"投稿推荐: {req.paper.title}", req.user_id,
                       {"task_id": task_id})
    long_mem.save_turn(session_id, "assistant",
                       f"推荐 {len(result.get('recommendations', []))} 个目标",
                       req.user_id, {"task_id": task_id})
    long_mem.save_preferences(req.user_id, merged_prefs)
    long_mem.save_recommendation(
        task_id=task_id,
        user_id=req.user_id,
        paper_id=f"API-{task_id}",
        paper_title=req.paper.title,
        paper_abstract=req.paper.abstract,
        paper_features=result.get("paper_features"),
        quality_estimate=req.quality.model_dump(),
        preferences_snapshot=merged_prefs,
        recommendations=result.get("recommendations", []),
        final_report=result.get("final_report", ""),
        execution_time_ms=execution_ms,
        model_used="deepseek-v4-pro",
    )

    return {
        "task_id": task_id,
        "session_id": session_id,
        "paper_title": req.paper.title,
        "thinking_trace": result.get("thinking_trace", []),
        "recommendations": result.get("recommendations", []),
        "submission_checklist": result.get("submission_checklist", {}),
        "submission_strategy": result.get("submission_strategy", {}),
        "comparison_matrix": result.get("comparison_matrix", {}),
        "final_report": result.get("final_report", ""),
        "execution_time_ms": execution_ms,
        "errors": result.get("errors", []),
    }


@app.get("/api/v1/recommend/{task_id}")
async def api_get_recommendation(task_id: str):
    """查询推荐任务结果（从长期记忆加载）"""
    long_mem = get_long_term_memory()
    rec = long_mem.load_recommendation(task_id)
    if not rec:
        raise HTTPException(404, f"任务 {task_id} 不存在")
    feedback = long_mem.load_feedback(task_id)
    rec["feedback"] = feedback
    return rec


@app.post("/api/v1/recommend/rerank")
async def api_rerank(req: RerankRequest):
    """偏好调整重排"""
    # 从长期记忆加载原始推荐
    long_mem = get_long_term_memory()
    rec = long_mem.load_recommendation(req.task_id)
    if not rec:
        raise HTTPException(404, f"任务 {req.task_id} 不存在")

    # 这里简化处理：重新执行推荐（生产环境应复用中间状态）
    return {"task_id": req.task_id, "status": "reranked",
            "message": "偏好已更新，请调用 POST /api/v1/recommend 重新推荐",
            "new_preferences": req.preferences.model_dump()}


# ══════════════════════════════════════════════════
# 记忆 API
# ══════════════════════════════════════════════════

@app.get("/api/v1/memory/short-term/{session_id}")
async def api_get_short_term(session_id: str):
    """获取短期记忆（会话上下文）"""
    short_mem = get_short_term_memory()
    context = short_mem.get_context(session_id)
    summary = short_mem.get_summary(session_id)
    return {
        "session_id": session_id,
        "turns": len(context),
        "context": context,
        "summary": summary,
        "full_context": short_mem.get_full_context(session_id),
    }


@app.delete("/api/v1/memory/short-term/{session_id}")
async def api_clear_short_term(session_id: str):
    """清除短期记忆"""
    short_mem = get_short_term_memory()
    short_mem.clear(session_id)
    return {"session_id": session_id, "status": "cleared"}


@app.get("/api/v1/memory/long-term/conversation/{session_id}")
async def api_get_conversation(session_id: str, limit: int = 50):
    """获取长期记忆中的对话历史"""
    long_mem = get_long_term_memory()
    history = long_mem.load_history(session_id, limit)
    return {"session_id": session_id, "turns": len(history), "history": history}


@app.get("/api/v1/memory/long-term/user/{user_id}/history")
async def api_get_user_history(user_id: str, limit: int = 100):
    """获取用户所有历史"""
    long_mem = get_long_term_memory()
    return {"user_id": user_id, "history": long_mem.load_user_history(user_id, limit)}


@app.get("/api/v1/memory/long-term/user/{user_id}/preferences")
async def api_get_preferences(user_id: str):
    """获取用户偏好"""
    long_mem = get_long_term_memory()
    prefs = long_mem.load_preferences(user_id)
    return {"user_id": user_id, "preferences": prefs}


@app.put("/api/v1/memory/long-term/user/{user_id}/preferences")
async def api_save_preferences(user_id: str, preferences: dict):
    """保存用户偏好"""
    long_mem = get_long_term_memory()
    long_mem.save_preferences(user_id, preferences)
    return {"user_id": user_id, "status": "saved"}


@app.get("/api/v1/memory/long-term/user/{user_id}/recommendations")
async def api_get_user_recommendations(user_id: str, limit: int = 20):
    """获取用户推荐历史"""
    long_mem = get_long_term_memory()
    recs = long_mem.load_user_recommendations(user_id, limit)
    return {"user_id": user_id, "count": len(recs), "recommendations": recs}


@app.get("/api/v1/memory/long-term/user/{user_id}/insights")
async def api_get_user_insights(user_id: str):
    """获取用户画像洞察"""
    long_mem = get_long_term_memory()
    return {"user_id": user_id, "insights": long_mem.load_insights(user_id)}


# ══════════════════════════════════════════════════
# 反馈 API
# ══════════════════════════════════════════════════

@app.post("/api/v1/feedback")
async def api_submit_feedback(req: FeedbackRequest):
    """提交推荐反馈"""
    long_mem = get_long_term_memory()
    long_mem.save_feedback(
        task_id=req.task_id,
        user_id=req.user_id,
        rating=req.rating,
        accepted_recommendation=req.accepted_recommendation,
        actual_submission_venue=req.actual_submission_venue,
        actual_result=req.actual_result,
        comments=req.comments,
    )
    return {"task_id": req.task_id, "status": "feedback_saved"}


@app.get("/api/v1/feedback/{task_id}")
async def api_get_feedback(task_id: str):
    """查询反馈"""
    long_mem = get_long_term_memory()
    fb = long_mem.load_feedback(task_id)
    if not fb:
        raise HTTPException(404, f"任务 {task_id} 无反馈")
    return fb


# ══════════════════════════════════════════════════
# 知识库 API
# ══════════════════════════════════════════════════

@app.get("/api/v1/venues")
async def api_list_venues(
    ccf_level: Optional[str] = Query(None, description="CCF级别过滤"),
    venue_type: Optional[str] = Query(None, description="conference / journal"),
    research_area: Optional[str] = Query(None, description="研究领域"),
):
    """列出会议/期刊"""
    from knowledge import get_knowledge_base
    kb = get_knowledge_base()
    venues = kb.get_all()

    if ccf_level:
        venues = [v for v in venues if v.get("ccf_level") == ccf_level]
    if venue_type:
        venues = [v for v in venues if v.get("type") == venue_type]
    if research_area:
        venues = [
            v for v in venues
            if any(research_area.lower() in a.lower()
                   for a in v.get("research_areas", []))
        ]

    return {"count": len(venues), "venues": [
        {"abbreviation": v["abbreviation"], "full_name": v["full_name"],
         "ccf_level": v.get("ccf_level"), "type": v.get("type"),
         "acceptance_rate": v.get("acceptance_rate"),
         "next_deadline": v.get("next_deadline")}
        for v in venues
    ]}


@app.get("/api/v1/venues/{venue_id}")
async def api_get_venue(venue_id: str):
    """获取会议/期刊详情"""
    from knowledge import get_knowledge_base
    from tools.venue_profiler import get_venue_profile

    profile = await get_venue_profile(venue_id)
    if not profile:
        raise HTTPException(404, f"Venue {venue_id} 不存在")
    return profile


@app.get("/api/v1/deadlines")
async def api_get_deadlines(days: int = Query(default=120, description="未来天数")):
    """查询即将截止的会议"""
    from knowledge import get_knowledge_base
    from tools.deadline_tracker import track_deadlines

    kb = get_knowledge_base()
    venues = kb.get_all()
    deadline_info = await track_deadlines(venues)

    return {
        "urgent_count": deadline_info["urgent_count"],
        "next_30_days": deadline_info["next_30_days"][:10],
        "all_deadlines": deadline_info["deadlines"][:20],
    }


# ══════════════════════════════════════════════════
# 健康检查
# ══════════════════════════════════════════════════

@app.get("/api/v1/health")
async def health_check():
    """健康检查"""
    db_status = check_connection()
    return {
        "service": "投稿推荐 Agent",
        "version": "1.0.0",
        "status": "healthy",
        "database": db_status,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/v1/stats")
async def api_stats(user_id: Optional[str] = None):
    """统计信息"""
    long_mem = get_long_term_memory()
    from memory.db import get_session
    from memory.models import RecommendationRecord, UserFeedback

    session = get_session()
    try:
        total_recs = session.query(RecommendationRecord).count()
        total_feedback = session.query(UserFeedback).count()
        avg_rating = session.query(UserFeedback.rating)
        avg_rating = avg_rating.filter(UserFeedback.rating > 0)
        ratings = [r[0] for r in avg_rating.all()]
        avg = sum(ratings) / len(ratings) if ratings else 0

        stats = {
            "total_recommendations": total_recs,
            "total_feedback": total_feedback,
            "average_rating": round(avg, 2),
        }
        if user_id:
            user_recs = long_mem.load_user_recommendations(user_id)
            stats["user"] = {"user_id": user_id, "total_recommendations": len(user_recs)}
        return stats
    finally:
        session.close()


# ══════════════════════════════════════════════════
# WebSocket (实时推送)
# ══════════════════════════════════════════════════

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket 实时通信"""
    await websocket.accept()
    short_mem = get_short_term_memory()

    try:
        # 发送历史上下文
        context = short_mem.get_full_context(session_id)
        await websocket.send_json({
            "type": "context",
            "session_id": session_id,
            "context": context,
        })

        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "chat":
                short_mem.add_turn(session_id, data.get("role", "user"),
                                   data.get("content", ""))
                await websocket.send_json({
                    "type": "ack",
                    "message": "已记录",
                })

            elif msg_type == "recommend":
                # 通过 WebSocket 触发推荐
                await websocket.send_json({
                    "type": "status",
                    "message": "推荐任务已提交，请通过 REST API 获取结果",
                })

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        print(f"WebSocket 断开: {session_id}")


# ══════════════════════════════════════════════════
# 启动入口
# ══════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
