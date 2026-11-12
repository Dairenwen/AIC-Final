"""投稿准备清单工具 — 格式检查、实验补充、Cover Letter 要点等"""
from typing import Optional
from models import get_model_service
from utils.json_helper import safe_json_parse, to_json
from utils.logger import get_logger

logger = get_logger(__name__)

PROMPT = """为以下论文生成针对目标会议/期刊的投稿准备清单。

## 论文特征
{paper_features}

## 目标
{venue_info}

## 匹配度
{match_details}

## 竞争分析
{competition_analysis}

输出 JSON：
```json
{{"format_checks":["格式要求"],"experiment_supplements":["实验补充建议"],"cover_letter_points":["要点"],"attachments":["附件"],"timeline_checklist":[{{"phase":"格式化","deadline_days_before":30,"tasks":["任务"]}}]}}
```"""


async def build_checklist(venue: dict, paper_features: dict, match_details: dict,
                           competition_analysis: dict, model: Optional[str] = None) -> dict:
    ms = get_model_service()
    prompt = PROMPT.format(paper_features=to_json(paper_features), venue_info=to_json(venue),
                           match_details=to_json(match_details), competition_analysis=to_json(competition_analysis))
    try:
        response = ms.chat(messages=[
            {"role": "system", "content": "请严格按 JSON 格式输出。"},
            {"role": "user", "content": prompt},
        ], model=model, temperature=0.3, json_mode=True)
        result = safe_json_parse(response) or {}
    except Exception:
        result = {}
    return {"venue": venue.get("abbreviation", ""),
            "format_checks": result.get("format_checks", ["确认页数和格式符合要求"]),
            "experiment_supplements": result.get("experiment_supplements", ["检查实验完整度"]),
            "cover_letter_points": result.get("cover_letter_points", ["强调论文创新点"]),
            "attachments": result.get("attachments", ["附录"]),
            "timeline_checklist": result.get("timeline_checklist", [])}
