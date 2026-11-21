"""论文特征抽取工具"""
from typing import Optional
from models import get_model_service
from utils.json_helper import safe_json_parse, to_json
from utils.logger import get_logger

logger = get_logger(__name__)

PROMPT = """你是一位资深学术审稿人。请从以下论文结构化解构结果中提取投稿匹配核心特征。

## 论文解析数据
{paper_json}

## 质量估计数据
{quality_json}

## 输出要求（严格 JSON）
```json
{{
  "sub_fields": ["子领域1"],
  "methodology_paradigm": "方法范式",
  "experiment_completeness": 0.0-1.0,
  "novelty_level": "incremental / substantial / breakthrough",
  "datasets_used": ["数据集名称"],
  "reference_venue_distribution": {{"NeurIPS": 5, "ICML": 3}},
  "key_techniques": ["关键技术"],
  "innovation_summary": "一句话创新点概述"
}}
```"""


async def extract_paper_features(parsed_paper: dict, quality_estimate: dict,
                                  model: Optional[str] = None) -> dict:
    ms = get_model_service()
    prompt = PROMPT.format(paper_json=to_json(parsed_paper), quality_json=to_json(quality_estimate))
    response = ms.chat(messages=[
        {"role": "system", "content": "你是一位资深学术审稿人。请严格按 JSON 格式输出。"},
        {"role": "user", "content": prompt},
    ], model=model, temperature=0.2, json_mode=True)
    features = safe_json_parse(response) or {}
    logger.info(f"论文特征提取完成: 子领域={features.get('sub_fields', [])}")
    return features
