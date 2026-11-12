"""
智研 · 投稿推荐 Agent — 主工作流（LangGraph 编排）

10 节点完整流水线：
任务接收 → 特征提取 → 候选检索 → 语义匹配 → 引用耦合 →
动态信息聚合 → 竞争分析 → 多目标排序 → 清单/策略 → 报告生成
"""

import asyncio
import uuid
from datetime import datetime
from typing import Optional

from config import Config, UserPreferences
from state import SubmissionRecommendState
from models import get_model_service
from knowledge import get_knowledge_base
from utils.json_helper import to_json
from utils.logger import get_logger

from tools.feature_extractor import extract_paper_features
from tools.venue_retriever import retrieve_candidate_venues
from tools.semantic_matcher import compute_semantic_match
from tools.citation_coupler import analyze_citation_coupling
from tools.venue_profiler import batch_get_venue_profiles
from tools.deadline_tracker import track_deadlines
from tools.trend_analyzer import batch_analyze_trends
from tools.competition_analyzer import batch_analyze_competition
from tools.acceptance_estimator import estimate_acceptance
from tools.checklist_builder import build_checklist
from tools.comparator import build_comparison_matrix
from tools.report_generator import generate_report, generate_interactive_data

logger = get_logger(__name__)


class SubmissionRecommendAgent:
    """投稿推荐 Agent — 编排完整推荐工作流"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.ms = get_model_service(self.config.model)
        self.kb = get_knowledge_base(self.config.retrieval)

    # ── 思考追踪 ──────────────────────────────────────────

    @staticmethod
    def _trace(step: str, label: str, summary: str, details: dict) -> dict:
        return {"step": step, "label": label, "summary": summary, "details": details}

    # ═══════════════════════════════════════════════════════
    # 工作流节点
    # ═══════════════════════════════════════════════════════

    async def node_receive_task(self, state: SubmissionRecommendState) -> dict:
        task_id = state.get("task_id") or f"SR-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
        logger.info(f"[{task_id}] 开始投稿推荐任务")
        parsed, quality = state.get("parsed_paper") or {}, state.get("quality_estimate") or {}
        warnings = []
        if not parsed: warnings.append("缺少论文结构化解析结果")
        if not quality: warnings.append("缺少质量估计数据")
        return {"task_id": task_id, "user_preferences": state.get("user_preferences") or {},
                "parsed_paper": parsed, "quality_estimate": quality,
                "compliance_result": state.get("compliance_result") or {},
                "iteration_count": state.get("iteration_count", 0), "errors": warnings}

    async def node_extract_features(self, state: SubmissionRecommendState) -> dict:
        logger.info(f"[{state['task_id']}] 提取论文特征...")
        features = await extract_paper_features(state.get("parsed_paper", {}), state.get("quality_estimate", {}))
        return {"paper_features": features}

    async def node_retrieve_candidates(self, state: SubmissionRecommendState) -> dict:
        logger.info(f"[{state['task_id']}] 检索候选...")
        candidates = await retrieve_candidate_venues(state.get("paper_features", {}),
                                                      state.get("user_preferences"), self.config.max_candidates)
        return {"candidate_venues": candidates}

    async def node_semantic_match(self, state: SubmissionRecommendState) -> dict:
        logger.info(f"[{state['task_id']}] 语义匹配...")
        candidates = state.get("candidate_venues", [])
        if not candidates: return {"match_scores": {}, "candidate_venues": []}
        matched = await compute_semantic_match(state.get("paper_features", {}), candidates,
                                               top_k=self.config.retrieval.rerank_top_k)
        match_scores = {v["abbreviation"]: v.get("match_score", {}) for v in matched}
        return {"candidate_venues": matched, "match_scores": match_scores}

    async def node_analyze_citations(self, state: SubmissionRecommendState) -> dict:
        logger.info(f"[{state['task_id']}] 引用耦合...")
        coupling = await analyze_citation_coupling(state.get("parsed_paper", {}), state.get("candidate_venues", []))
        match_scores = state.get("match_scores", {})
        for vid, score in coupling.get("coupling_scores", {}).items():
            if vid in match_scores: match_scores[vid]["citation_coupling"] = score
        return {"match_scores": match_scores,
                "venue_dynamic_info": {**(state.get("venue_dynamic_info") or {}), "citation_analysis": coupling}}

    async def node_aggregate_dynamic_info(self, state: SubmissionRecommendState) -> dict:
        logger.info(f"[{state['task_id']}] 动态信息聚合...")
        candidates = state.get("candidate_venues", [])
        deadline_info = await track_deadlines(candidates)
        venue_ids = [v["abbreviation"] for v in candidates]
        profiles = await batch_get_venue_profiles(venue_ids)
        trends = await batch_analyze_trends(candidates[:5], state.get("paper_features", {}))
        return {"venue_dynamic_info": {**(state.get("venue_dynamic_info") or {}),
                "deadlines": deadline_info, "profiles": profiles,
                "trends": {t.get("venue", ""): t for t in trends}}}

    async def node_analyze_competition(self, state: SubmissionRecommendState) -> dict:
        logger.info(f"[{state['task_id']}] 竞争分析...")
        candidates = state.get("candidate_venues", [])[:10]
        competition = await batch_analyze_competition(candidates, state.get("paper_features", {}),
                                                       state.get("quality_estimate", {}))
        return {"competitive_analysis": {"results": {c.get("venue", ""): c for c in competition},
                                          "top_competitors": competition[:3]}}

    async def node_rank_and_recommend(self, state: SubmissionRecommendState) -> dict:
        task_id = state['task_id']
        logger.info(f"[{task_id}] 多目标排序...")
        candidates = state.get("candidate_venues", [])
        match_scores = state.get("match_scores", {})
        competition = state.get("competitive_analysis", {}).get("results", {})
        prefs = UserPreferences(**(state.get("user_preferences") or {}))

        # 先计算 rank_score（不需要 LLM，很快）
        # 加载反馈驱动的个性化权重
        from memory.long_term import get_long_term_memory
        user_id = state.get("user_id", "default")
        feedback_adj = get_long_term_memory().compute_feedback_adjustments(user_id)

        pre_scored = []
        for venue in candidates:
            vid = venue["abbreviation"]
            ms = match_scores.get(vid, {})
            comp = competition.get(vid, {})
            match_overall = ms.get("overall", 0.5)
            competitiveness = comp.get("overall_competitiveness", 0.5)
            deadline_penalty = 0.0
            for d in state.get("venue_dynamic_info", {}).get("deadlines", {}).get("deadlines", []):
                if d.get("venue") == vid:
                    days = d.get("days_remaining")
                    if days is not None:
                        if days < 14: deadline_penalty = 0.3
                        elif days < 30: deadline_penalty = 0.15
                    break
            review_penalty = 0.1 if venue.get("avg_review_weeks", 0) > prefs.max_review_weeks else 0.0

            # 反馈闭环: 历史录用/拒稿结果影响排序
            fb_boost = feedback_adj.get(vid.upper(), 0)
            fb_boost = max(-0.10, min(0.10, fb_boost))  # 限制幅度

            rank_score = (match_overall * 0.43 + competitiveness * 0.22 +
                          (1 - deadline_penalty) * 0.13 + (1 - review_penalty) * 0.10 +
                          (0.1 if venue.get("is_oa") == prefs.prefer_oa else 0) * 0.05 +
                          (0.5 + fb_boost) * 0.07)  # 反馈因子占 7%
            pre_scored.append({"venue": venue, "match_score": ms, "rank_score": rank_score, "competition": comp})

        if feedback_adj:
            logger.info(f"[{task_id}] 已应用 {len(feedback_adj)} 条反馈权重调整")

        # 并行调用 estimate_acceptance（所有 venue 同时发出）
        async def _estimate_one(item):
            acceptance = await estimate_acceptance(
                venue=item["venue"], match_score=item["match_score"],
                paper_features=state.get("paper_features", {}),
                competition_analysis=item["competition"])
            return {**item, "acceptance": acceptance}

        scored = await asyncio.gather(*[_estimate_one(item) for item in pre_scored])

        scored.sort(key=lambda x: x["rank_score"], reverse=True)
        n = len(scored)

        # 将录用概率文字映射为数值(用于分档判断)
        prob_map = {"低": 0.15, "中等偏低": 0.25, "中等": 0.45,
                    "中等偏高": 0.60, "高": 0.75}

        recommendations = []
        for i, item in enumerate(scored):
            percentile = i / max(n, 1)
            rank_tier = "sprint" if percentile < 0.3 else ("match" if percentile < 0.7 else "safety")

            acc_prob_str = item["acceptance"].get("estimated_probability_range", "中等")
            acc_prob_val = prob_map.get(acc_prob_str, 0.45)
            acc_rate_val = item["venue"].get("acceptance_rate", 0.25)
            ccf = item["venue"].get("ccf_level", "")

            # 保底档修正: 必须确实"容易中"才算保底
            # 条件: 录用概率不低于中等偏低, 且接收率不低于15%
            if rank_tier == "safety" and (acc_prob_val < 0.25 or acc_rate_val < 0.15):
                rank_tier = "match"  # 降级为匹配而非保底

            # 冲刺档修正: 录用概率太低的不应冲刺
            if rank_tier == "sprint" and acc_prob_val < 0.20 and acc_rate_val < 0.18:
                rank_tier = "match"

            match_details = {"strengths": item["competition"].get("strengths_vs_peers", []),
                             "risks": item["competition"].get("weaknesses_vs_peers", []),
                             "differentiation": item["competition"].get("analysis_summary", "")}
            recommendations.append({
                "venue": item["venue"], "tier": rank_tier, "match_score": item["match_score"],
                "match_details": match_details,
                "estimated_acceptance_prob": acc_prob_str,
                "confidence": item["acceptance"].get("confidence", 0.6),
                "rank_score": item["rank_score"],
                "risks": match_details["risks"], "strengths": match_details["strengths"],
                "differentiation": match_details["differentiation"],
            })

        sprint = [r for r in recommendations if r["tier"] == "sprint"][:prefs.sprint_tier_count]
        match_list = [r for r in recommendations if r["tier"] == "match"][:prefs.match_tier_count]
        # 保底档重新按接收率+录用概率排序，优先推荐容易中的
        safety_pool = [r for r in recommendations if r["tier"] == "safety"]
        safety_pool.sort(key=lambda r: (
            prob_map.get(r["estimated_acceptance_prob"], 0.45) * 0.5 +
            r["venue"].get("acceptance_rate", 0.25) * 0.3 +
            (0.2 if r["venue"].get("ccf_level") in ("CCF-B", "CCF-C") else 0)
        ), reverse=True)
        safety = safety_pool[:prefs.safety_tier_count]
        final = sprint + match_list + safety

        logger.info(f"[{task_id}] 推荐排序: 冲刺{len(sprint)}/匹配{len(match_list)}/保底{len(safety)}")
        return {"recommendations": final}

    async def node_build_checklist(self, state: SubmissionRecommendState) -> dict:
        recommendations = state.get("recommendations", [])
        if not recommendations: return {"submission_checklist": {}}
        top_rec = recommendations[0]
        checklist = await build_checklist(venue=top_rec["venue"],
                                           paper_features=state.get("paper_features", {}),
                                           match_details=top_rec.get("match_details", {}),
                                           competition_analysis=top_rec.get("competition", {}))
        return {"submission_checklist": checklist}

    async def node_generate_strategy(self, state: SubmissionRecommendState) -> dict:
        recommendations = state.get("recommendations", [])
        deadlines = state.get("venue_dynamic_info", {}).get("deadlines", {}).get("deadlines", [])
        timeline = []
        for d in deadlines:
            rec = next((r for r in recommendations if r["venue"]["abbreviation"] == d["venue"]), None)
            if rec:
                timeline.append({"phase": f"投稿: {d['venue']}", "deadline": d.get("deadline", ""),
                                 "days_remaining": d.get("days_remaining"), "tier": rec.get("tier", ""),
                                 "action": "立即准备" if d.get("urgency") in ("urgent", "warning") else "按计划准备"})
        timeline.sort(key=lambda x: x.get("days_remaining", 999) or 999)
        strategy = {"primary_target": recommendations[0] if recommendations else None,
                    "timeline": timeline,
                    "fallback_plan": f"若首选未中，依次尝试: {', '.join(r['venue']['abbreviation'] for r in recommendations[1:4])}" if len(recommendations) > 1 else "无"}
        return {"submission_strategy": strategy}

    async def node_generate_report(self, state: SubmissionRecommendState) -> dict:
        logger.info(f"[{state['task_id']}] 生成报告...")
        comparison = await build_comparison_matrix(state.get("recommendations", []))
        report = await generate_report(
            paper_features=state.get("paper_features", {}),
            paper_summary=state.get("parsed_paper", {}),
            user_preferences=state.get("user_preferences", {}),
            recommendations=state.get("recommendations", []),
            competition_results=list((state.get("competitive_analysis", {}).get("results", {})).values()),
            deadline_info=state.get("venue_dynamic_info", {}).get("deadlines", {}),
            checklist=state.get("submission_checklist", {}))
        interactive = await generate_interactive_data(state.get("recommendations", []), comparison)
        return {"final_report": report, "interactive_data": interactive, "comparison_matrix": comparison}

    # ═══════════════════════════════════════════════════════
    # 主运行方法
    # ═══════════════════════════════════════════════════════

    async def run(self, paper_id: str, parsed_paper: dict,
                  quality_estimate: Optional[dict] = None,
                  compliance_result: Optional[dict] = None,
                  user_preferences: Optional[dict] = None,
                  user_id: str = "default") -> SubmissionRecommendState:
        state: SubmissionRecommendState = {
            "task_id": f"SR-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}",
            "paper_id": paper_id, "user_id": user_id,
            "user_preferences": user_preferences or {},
            "parsed_paper": parsed_paper, "quality_estimate": quality_estimate or {},
            "compliance_result": compliance_result or {},
            "thinking_trace": [],
            "iteration_count": 0, "user_satisfied": True,
        }
        T = state["thinking_trace"]
        logger.info(f"══════ 开始投稿推荐: {state['task_id']} ══════")
        try:
            state.update(await self.node_receive_task(state))
            for err in state.get("errors", []): logger.warning(f"  [!] {err}")
            T.append(self._trace("receive_task", "接收任务",
                f"任务 {state['task_id']} 已创建，论文: {parsed_paper.get('title','')[:50]}",
                {"task_id": state["task_id"], "paper_title": parsed_paper.get("title",""),
                 "quality": {k: v for k, v in (quality_estimate or {}).items()}}))

            state.update(await self.node_extract_features(state))
            feats = state.get("paper_features", {})
            T.append(self._trace("extract_features", "提取论文特征",
                f"创新层次: {feats.get('novelty_level','?')}，实验完整度: {feats.get('experiment_completeness',0)}，"
                f"方法: {feats.get('methodology_paradigm','?')}，领域: {feats.get('sub_fields',[])}",
                {"sub_fields": feats.get("sub_fields", []),
                 "methodology_paradigm": feats.get("methodology_paradigm", ""),
                 "novelty_level": feats.get("novelty_level", ""),
                 "experiment_completeness": feats.get("experiment_completeness", 0),
                 "key_techniques": feats.get("key_techniques", []),
                 "innovation_summary": feats.get("innovation_summary", "")}))

            state.update(await self.node_retrieve_candidates(state))
            cands = state.get("candidate_venues", [])
            T.append(self._trace("retrieve_candidates", "检索候选会议/期刊",
                f"从知识库检索到 {len(cands)} 个候选",
                {"candidate_count": len(cands),
                 "top_candidates": [{"abbrev": v["abbreviation"], "ccf": v.get("ccf_level",""),
                                     "type": v.get("type","")} for v in cands[:5]],
                 "ccf_levels": list(set(v.get("ccf_level","") for v in cands))}))
            logger.info(f"  → 候选: {len(cands)}")

            state.update(await self.node_semantic_match(state))
            ms = state.get("match_scores", {})
            top3 = sorted(ms.items(), key=lambda x: x[1].get("overall", 0), reverse=True)[:3]
            top3_str = ", ".join(a + "(" + str(round(s.get("overall", 0) * 100)) + "%)" for a, s in top3)
            T.append(self._trace("semantic_match", "语义匹配",
                f"Top3: {top3_str}",
                {"top3_matches": [{"abbrev": a, "overall": s.get("overall", 0),
                    "topic": s.get("topic_similarity", 0),
                    "methodology": s.get("methodology_alignment", 0),
                    "novelty_fit": s.get("novelty_level_fit", 0)} for a, s in top3],
                 "total_matched": len(ms)}))

            state.update(await self.node_analyze_citations(state))
            cit = state.get("venue_dynamic_info", {}).get("citation_analysis", {})
            top_cit = cit.get("top_cited_venues", [])[:3]
            T.append(self._trace("analyze_citations", "引用耦合分析",
                f"分析 {cit.get('total_references',0)} 篇参考文献，"
                f"引用最多: {', '.join(c['venue']+'('+str(c['count'])+')' for c in top_cit) if top_cit else '无引用数据'}",
                {"total_references": cit.get("total_references", 0),
                 "top_cited": top_cit,
                 "ref_distribution": cit.get("reference_distribution", {})}))

            state.update(await self.node_aggregate_dynamic_info(state))
            dl = state.get("venue_dynamic_info", {}).get("deadlines", {})
            urgent = dl.get("urgent_venues", [])
            trends = state.get("venue_dynamic_info", {}).get("trends", {})
            trend_high = [(v, t.get("trend_fit_score", 0)) for v, t in trends.items()
                          if t.get("trend_fit_score", 0) > 0.7]
            T.append(self._trace("aggregate_dynamic_info", "动态信息聚合",
                f"紧急截稿: {len(urgent)} 个，趋势匹配高: {len(trend_high)} 个",
                {"urgent_venues": urgent,
                 "next_30_days": len(dl.get("next_30_days", [])),
                 "trend_high_fit": [{"venue": v, "score": s} for v, s in trend_high]}))

            state.update(await self.node_analyze_competition(state))
            comp = state.get("competitive_analysis", {}).get("results", {})
            top_comp = sorted(comp.items(), key=lambda x: x[1].get("overall_competitiveness", 0), reverse=True)[:3]
            top_comp_str = ", ".join(a + "(" + str(round(c.get("overall_competitiveness", 0) * 100)) + "%)" for a, c in top_comp)
            T.append(self._trace("analyze_competition", "竞争分析",
                f"竞争力最强: {top_comp_str}",
                {"top3_competitiveness": [{"abbrev": a, "score": c.get("overall_competitiveness", 0),
                    "strengths": c.get("strengths_vs_peers", [])[:2],
                    "weaknesses": c.get("weaknesses_vs_peers", [])[:2]} for a, c in top_comp]}))

            state.update(await self.node_rank_and_recommend(state))
            recs = state.get("recommendations", [])
            sprint_n = sum(1 for r in recs if r["tier"] == "sprint")
            match_n = sum(1 for r in recs if r["tier"] == "match")
            safety_n = sum(1 for r in recs if r["tier"] == "safety")
            top = recs[0] if recs else None
            rank_summary = f"冲刺{sprint_n}/匹配{match_n}/保底{safety_n}"
            if top:
                abbrev = top["venue"]["abbreviation"]
                tier = top["tier"]
                pct = round(top["match_score"].get("overall", 0) * 100)
                rank_summary += f"，首选: {abbrev}({tier}, 匹配度{pct}%)"
            T.append(self._trace("rank_and_recommend", "多目标排序与推荐", rank_summary,
                {"tier_counts": {"sprint": sprint_n, "match": match_n, "safety": safety_n},
                 "top_recommendation": {"abbrev": top["venue"]["abbreviation"], "tier": top["tier"],
                     "ccf": top["venue"].get("ccf_level",""), "match_overall": top["match_score"].get("overall",0),
                     "acceptance_prob": top.get("estimated_acceptance_prob",""),
                     "rank_score": top.get("rank_score", 0),
                     "strengths": top.get("strengths", [])[:3],
                     "risks": top.get("risks", [])[:3]} if top else None,
                 "ranking_formula": "rank=匹配*0.45+竞争力*0.25+截稿*0.15+审稿*0.10+OA*0.05; 保底需录用概率>=中等偏低且接收率>=15%"}))

            state.update(await self.node_build_checklist(state))
            cl = state.get("submission_checklist", {})
            T.append(self._trace("build_checklist", "生成投稿清单",
                f"针对 {cl.get('venue','首选')} 生成清单: "
                f"格式检查{len(cl.get('format_checks',[]))}项, "
                f"实验补充{len(cl.get('experiment_supplements',[]))}项",
                {"venue": cl.get("venue", ""),
                 "key_format_checks": cl.get("format_checks", [])[:3],
                 "key_supplements": cl.get("experiment_supplements", [])[:3],
                 "cover_letter_points": cl.get("cover_letter_points", [])[:3]}))

            state.update(await self.node_generate_strategy(state))
            st = state.get("submission_strategy", {})
            T.append(self._trace("generate_strategy", "制定投稿策略",
                f"首选: {st.get('primary_target',{}).get('venue',{}).get('abbreviation','?') if st.get('primary_target') else '?'}，"
                f"时间线: {len(st.get('timeline',[]))} 个节点",
                {"primary_target": st.get("primary_target", {}).get("venue", {}).get("abbreviation", ""),
                 "timeline": [{"venue": t.get("phase",""), "deadline": t.get("deadline",""),
                               "days": t.get("days_remaining"), "tier": t.get("tier","")}
                              for t in st.get("timeline", [])[:5]],
                 "fallback_plan": st.get("fallback_plan", "")}))

            state.update(await self.node_generate_report(state))
            report_len = len(state.get("final_report", ""))
            comp_matrix = state.get("comparison_matrix", {})
            T.append(self._trace("generate_report", "生成推荐报告",
                f"报告 {report_len} 字符，对比矩阵 {comp_matrix.get('total_candidates',0)} 个候选",
                {"report_length": report_len,
                 "comparison_tiers": comp_matrix.get("tier_distribution", {})}))

            logger.info(f"══════ 投稿推荐完成: {state['task_id']} ══════")
        except Exception as e:
            logger.error(f"工作流异常: {e}", exc_info=True)
            state["errors"] = state.get("errors", []) + [str(e)]
        return state

    async def rerank(self, state: SubmissionRecommendState, updated_preferences: dict) -> SubmissionRecommendState:
        state["user_preferences"] = updated_preferences
        state["iteration_count"] = state.get("iteration_count", 0) + 1
        state.update(await self.node_rank_and_recommend(state))
        state.update(await self.node_generate_report(state))
        return state


_agent: Optional[SubmissionRecommendAgent] = None


def get_agent(config: Optional[Config] = None) -> SubmissionRecommendAgent:
    global _agent
    if _agent is None or (
        config is not None
        and (
            _agent.config.model.api_base != config.model.api_base
            or _agent.config.model.primary_model != config.model.primary_model
        )
    ):
        _agent = SubmissionRecommendAgent(config)
    return _agent


async def recommend_submission(paper_id: str, parsed_paper: dict,
                               quality_estimate: Optional[dict] = None,
                               compliance_result: Optional[dict] = None,
                               user_preferences: Optional[dict] = None,
                               config: Optional[Config] = None) -> dict:
    """投稿推荐便捷接口"""
    agent = get_agent(config)
    state = await agent.run(paper_id=paper_id, parsed_paper=parsed_paper,
                            quality_estimate=quality_estimate,
                            compliance_result=compliance_result,
                            user_preferences=user_preferences)
    return {"task_id": state.get("task_id"), "paper_id": state.get("paper_id"),
            "thinking_trace": state.get("thinking_trace", []),
            "recommendations": state.get("recommendations", []),
            "submission_checklist": state.get("submission_checklist", {}),
            "submission_strategy": state.get("submission_strategy", {}),
            "final_report": state.get("final_report", ""),
            "comparison_matrix": state.get("comparison_matrix", {}),
            "interactive_data": state.get("interactive_data", {}),
            "errors": state.get("errors", [])}
