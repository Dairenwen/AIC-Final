"""
智研 · 投稿推荐 Agent — 配置文件
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# 自动加载 .env 文件
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())


@dataclass
class ModelConfig:
    """多模型协同配置"""
    primary_model: str = "deepseek-v4-pro"          # 主推理模型
    auxiliary_model: str = "deepseek-v4-flash"       # 辅助模型（交互问答、文本润色）
    embedding_model: str = "BAAI/bge-m3"             # 嵌入模型
    reranker_model: str = "BAAI/bge-reranker-v2-m3" # 重排模型
    vision_model: Optional[str] = None               # 多模态模型（图表识别）

    api_base: str = field(default_factory=lambda: os.getenv(
        "ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic"))
    api_key: str = field(default_factory=lambda: os.getenv(
        "ANTHROPIC_AUTH_TOKEN", ""))


@dataclass
class RetrievalConfig:
    """三阶段检索配置"""
    es_host: str = "http://localhost:9200"
    es_index: str = "venue_metadata"
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "venue_vectors"
    bm25_top_k: int = 150
    semantic_top_k: int = 30
    rerank_top_k: int = 10
    similarity_threshold: float = 0.65


@dataclass
class MatchWeights:
    """多维匹配度权重分配（总和=1.0）"""
    topic_similarity: float = 0.30
    methodology_alignment: float = 0.20
    experiment_completeness_fit: float = 0.12
    novelty_level_fit: float = 0.15
    citation_coupling: float = 0.08
    venue_prestige_match: float = 0.10
    deadline_feasibility: float = 0.05


@dataclass
class UserPreferences:
    """投稿偏好"""
    target_ccf_levels: list = field(default_factory=lambda: ["CCF-A", "CCF-B"])
    target_caai_levels: list = field(default_factory=lambda: ["CAAI-A", "CAAI-B"])
    max_review_weeks: int = 16
    prefer_oa: bool = False
    max_publication_fee: float = 0.0
    preferred_locations: list = field(default_factory=list)
    excluded_venues: list = field(default_factory=list)
    sprint_tier_count: int = 3
    match_tier_count: int = 5
    safety_tier_count: int = 3


@dataclass
class Config:
    model: ModelConfig = field(default_factory=ModelConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    weights: MatchWeights = field(default_factory=MatchWeights)
    max_candidates: int = 50
    output_dir: str = "./output"
    log_level: str = "INFO"
