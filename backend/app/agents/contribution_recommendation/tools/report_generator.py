"""推荐报告生成工具 — 汇总分析结果，生成结构化 Markdown 报告"""
from typing import Optional
from datetime import datetime
from models import get_model_service
from utils.json_helper import to_json
from utils.logger import get_logger

logger = get_logger(__name__)

REPORT_PROMPT = """你是一位专业的学术投稿策略顾问。请基于以下分析数据生成结构化投稿推荐报告。

## 论文信息
{paper_summary}

## 用户偏好
{user_prefs}

## 推荐结果
{recommendations_json}

## 竞争分析摘要
{competition_summary}

## 截止日期
{deadline_summary}

请生成完整的 Markdown 报告，包含：
# 投稿推荐报告
## 一、论文概要
## 二、推荐结果总览（冲刺/匹配/保底）
## 三、首选推荐详细分析
## 四、备选方案对比
## 五、投稿准备清单
## 六、建议投稿时间线
## 七、风险提示与建议

直接输出 Markdown，不要额外解释，不要使用 emoji 图标。"""


async def generate_report(paper_features: dict, paper_summary: dict,
                          user_preferences: dict, recommendations: list[dict],
                          competition_results: list[dict], deadline_info: dict,
                          checklist: dict, model: Optional[str] = None) -> str:
    ms = get_model_service()
    comp_summary = [f"- {ca.get('venue', '')}: 竞争力 {ca.get('overall_competitiveness', 0):.2f}"
                    for ca in competition_results[:5]]
    dl_summary = []
    for d in deadline_info.get("deadlines", [])[:5]:
        days = d.get("days_remaining")
        dl_summary.append(f"- {d.get('venue', '')}: 截稿 {d.get('deadline', '')} (剩余 {days}天)")
    prompt = REPORT_PROMPT.format(
        paper_summary=to_json(paper_summary), user_prefs=to_json(user_preferences),
        recommendations_json=to_json(recommendations),
        competition_summary="\n".join(comp_summary) or "暂无",
        deadline_summary="\n".join(dl_summary) or "暂无")
    try:
        response = ms.chat(messages=[
            {"role": "system", "content": "你是学术投稿策略顾问。请生成结构化投稿推荐报告。"},
            {"role": "user", "content": prompt},
        ], model=model, temperature=0.4, max_tokens=8192)
    except Exception as e:
        logger.error(f"报告生成失败: {e}")
        response = _fallback_report(paper_features, recommendations, deadline_info)
    logger.info(f"推荐报告生成完成: {len(response)} 字符")
    return response


def _fallback_report(paper_features: dict, recommendations: list[dict], deadline_info: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = ["# 投稿推荐报告", f"\n> 生成时间: {now}", "\n## 一、论文概要",
             f"\n- 研究领域: {', '.join(paper_features.get('sub_fields', ['未指定']))}",
             f"- 方法范式: {paper_features.get('methodology_paradigm', '未指定')}",
             f"- 创新层次: {paper_features.get('novelty_level', '未指定')}",
             "\n## 二、推荐结果"]
    tiers = {"sprint": "冲刺档", "match": "匹配档", "safety": "保底档"}
    for rec in recommendations:
        tier_label = tiers.get(rec.get("tier", ""), rec.get("tier", ""))
        venue = rec.get("venue", {})
        ms = rec.get("match_score", {})
        lines.append(f"\n### {tier_label}: {venue.get('abbreviation', '')}")
        lines.append(f"- 全称: {venue.get('full_name', '')}")
        lines.append(f"- CCF级别: {venue.get('ccf_level', '')} | 综合匹配度: {ms.get('overall', 0):.2%}")
        lines.append(f"- 截稿: {venue.get('next_deadline', 'N/A')} | 录用概率: {rec.get('estimated_acceptance_prob', 'N/A')}")
    lines.append("\n## 三、风险提示\n\n- 建议在投稿前完成所有实验补充和格式检查")
    return "\n".join(lines)


async def generate_interactive_data(recommendations: list[dict], comparison_matrix: dict) -> dict:
    radar_data = []
    for rec in recommendations[:5]:
        ms = rec.get("match_score", {})
        radar_data.append({
            "venue": rec.get("venue", {}).get("abbreviation", ""),
            "labels": ["主题相似度", "方法对齐度", "实验完整度", "创新层次", "引用耦合"],
            "values": [ms.get("topic_similarity", 0), ms.get("methodology_alignment", 0),
                       ms.get("experiment_completeness_fit", 0), ms.get("novelty_level_fit", 0),
                       ms.get("citation_coupling", 0)],
        })
    return {"radar_data": radar_data, "comparison_matrix": comparison_matrix,
            "recommendations": recommendations}
