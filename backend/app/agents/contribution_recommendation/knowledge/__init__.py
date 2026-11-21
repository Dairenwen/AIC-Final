"""期刊知识层 — 结构化会议/期刊元数据库 + 向量知识库 + 动态数据更新"""
import json
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta
from config import RetrievalConfig
from utils.logger import get_logger

logger = get_logger(__name__)
_data_dir = Path(__file__).parent  # knowledge/ 目录

# ═══════════════════════════════════════════════════════════
# 预警期刊/会议名单 (默认自动剔除)
#   来源: 中科院预警名单 / 各高校预警 / COPE 违规记录
#   用户可通过 excluded_venues 覆盖额外条目
# ═══════════════════════════════════════════════════════════
WARNING_VENUES: set[str] = set()
_warning_path = _data_dir / "warning_venues.txt"
if _warning_path.exists():
    with open(_warning_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#"):
                WARNING_VENUES.add(_line.upper())

# 中英文研究领域互译映射
_AREA_TRANSLATIONS = {
    # 中文 → 英文
    "机器学习": "machine learning", "深度学习": "deep learning",
    "神经网络": "neural network", "计算机视觉": "computer vision",
    "自然语言处理": "nlp", "计算语言学": "computational linguistics",
    "机器翻译": "machine translation", "文本生成": "text generation",
    "文本挖掘": "text mining", "信息抽取": "information extraction",
    "情感分析": "sentiment analysis", "对话系统": "dialogue system",
    "图像识别": "image recognition", "目标检测": "object detection",
    "图像生成": "image generation", "3d视觉": "3d vision",
    "3d重建": "3d reconstruction", "图像理解": "image understanding",
    "图像处理": "image processing", "视觉推理": "visual reasoning",
    "视觉认知": "visual cognition", "模式识别": "pattern recognition",
    "图像分析": "image analysis", "优化理论": "optimization",
    "优化": "optimization", "概率方法": "probabilistic method",
    "统计学习": "statistical learning", "强化学习": "reinforcement learning",
    "表示学习": "representation learning", "生成模型": "generative model",
    "深度学习理论": "deep learning theory", "学习系统": "learning system",
    "人工智能": "artificial intelligence", "知识图谱": "knowledge graph",
    "知识表示": "knowledge representation", "规划": "planning",
    "多智能体": "multi-agent", "机器人": "robotics",
    "数据挖掘": "data mining", "知识发现": "knowledge discovery",
    "大数据分析": "big data analytics", "图挖掘": "graph mining",
    # 补充英文 → 中文（反向映射 + 模糊到宽泛类别）
    "transfer learning": "机器学习", "cross-modal": "计算机视觉",
    "multimodal": "计算机视觉", "multi-modal": "计算机视觉",
    "vision": "计算机视觉", "language model": "自然语言处理",
    "llm": "自然语言处理", "large language model": "自然语言处理",
    "transformer": "深度学习", "diffusion": "计算机视觉",
    "gan": "生成模型", "contrastive learning": "表示学习",
    "self-supervised": "表示学习", "unsupervised": "机器学习",
    "supervised": "机器学习", "semi-supervised": "机器学习",
    "few-shot": "机器学习", "zero-shot": "机器学习",
    "meta learning": "机器学习", "federated learning": "机器学习",
    "graph neural": "神经网络", "graph network": "神经网络",
    "recurrent neural": "神经网络", "convolutional neural": "计算机视觉",
    "object detection": "计算机视觉", "segmentation": "计算机视觉",
    "image classification": "计算机视觉", "video understanding": "计算机视觉",
    "attention": "深度学习", "dynamic routing": "表示学习",
    "routing": "表示学习", "cross modal": "计算机视觉",
    "cross-modal": "计算机视觉", "multimodal learning": "计算机视觉",
    "cross-modal learning": "计算机视觉",
    "text classification": "自然语言处理", "ner": "自然语言处理",
    "question answering": "自然语言处理", "summarization": "自然语言处理",
    "parsing": "自然语言处理", "speech": "人工智能",
    "time series": "数据挖掘", "anomaly detection": "数据挖掘",
    "clustering": "数据挖掘", "recommendation": "数据挖掘",
    "bayesian": "统计学习", "causal": "统计学习",
    "rl": "强化学习", "imitation learning": "强化学习",
    "policy gradient": "强化学习", "knowledge distillation": "深度学习",
    "pruning": "深度学习", "quantization": "深度学习",
    "nas": "深度学习", "automl": "机器学习",
}


def _normalize_areas(areas: list[str]) -> set[str]:
    """将中英文研究领域统一展开为双语集合"""
    result = set()
    for a in areas:
        al = a.strip().lower()
        result.add(al)
        # 查找互译
        for cn, en in _AREA_TRANSLATIONS.items():
            if al == cn.lower():
                result.add(en)
            elif al == en:
                result.add(cn.lower())
    return result

BUILTIN_VENUES = [
    # === CCF-A 会议 ===
    {"type": "conference", "abbreviation": "NeurIPS",
     "full_name": "Conference on Neural Information Processing Systems",
     "ccf_level": "CCF-A", "caai_level": "CAAI-A",
     "research_areas": ["机器学习", "深度学习", "神经网络", "优化理论", "强化学习", "计算机视觉", "NLP"],
     "acceptance_rate": 0.258, "avg_review_weeks": 11, "review_model": "Double-blind",
     "publication_fee": 0, "is_oa": False, "next_deadline": "2026-05-15", "notification_date": "2026-09-15",
     "aims_scope": "所有与神经信息处理系统相关的理论、算法和应用"},
    {"type": "conference", "abbreviation": "ICML",
     "full_name": "International Conference on Machine Learning",
     "ccf_level": "CCF-A", "caai_level": "CAAI-A",
     "research_areas": ["机器学习", "深度学习", "统计学习", "优化", "概率方法"],
     "acceptance_rate": 0.275, "avg_review_weeks": 10, "review_model": "Double-blind",
     "publication_fee": 0, "is_oa": False, "next_deadline": "2027-01-15", "notification_date": "2027-04-30"},
    {"type": "conference", "abbreviation": "CVPR",
     "full_name": "IEEE/CVF Conference on Computer Vision and Pattern Recognition",
     "ccf_level": "CCF-A", "caai_level": "CAAI-A",
     "research_areas": ["计算机视觉", "图像识别", "目标检测", "图像生成", "3D视觉"],
     "acceptance_rate": 0.262, "avg_review_weeks": 8, "review_model": "Double-blind",
     "publication_fee": 0, "is_oa": True, "next_deadline": "2026-11-10", "notification_date": "2027-02-28"},
    {"type": "conference", "abbreviation": "ICCV",
     "full_name": "International Conference on Computer Vision",
     "ccf_level": "CCF-A", "caai_level": "CAAI-A",
     "research_areas": ["计算机视觉", "图像理解", "3D重建", "视觉推理"],
     "acceptance_rate": 0.245, "avg_review_weeks": 9, "review_model": "Double-blind",
     "publication_fee": 0, "is_oa": False, "next_deadline": "2027-03-15", "notification_date": "2027-07-01"},
    {"type": "conference", "abbreviation": "ACL",
     "full_name": "Annual Meeting of the Association for Computational Linguistics",
     "ccf_level": "CCF-A", "caai_level": "CAAI-A",
     "research_areas": ["自然语言处理", "计算语言学", "机器翻译", "文本生成"],
     "acceptance_rate": 0.232, "avg_review_weeks": 9, "review_model": "Double-blind",
     "publication_fee": 0, "is_oa": True, "next_deadline": "2026-12-15", "notification_date": "2027-03-30"},
    {"type": "conference", "abbreviation": "AAAI",
     "full_name": "AAAI Conference on Artificial Intelligence",
     "ccf_level": "CCF-A", "caai_level": "CAAI-A",
     "research_areas": ["人工智能", "机器学习", "知识图谱", "规划", "多智能体", "计算机视觉", "NLP"],
     "acceptance_rate": 0.235, "avg_review_weeks": 9, "review_model": "Double-blind",
     "publication_fee": 0, "is_oa": True, "next_deadline": "2027-08-15", "notification_date": "2027-11-15"},
    {"type": "conference", "abbreviation": "IJCAI",
     "full_name": "International Joint Conference on Artificial Intelligence",
     "ccf_level": "CCF-A", "caai_level": "CAAI-A",
     "research_areas": ["人工智能", "知识表示", "规划", "机器学习", "多智能体", "机器人"],
     "acceptance_rate": 0.21, "avg_review_weeks": 8, "review_model": "Double-blind",
     "publication_fee": 0, "is_oa": True, "next_deadline": "2027-01-20", "notification_date": "2027-04-20"},
    {"type": "conference", "abbreviation": "ICLR",
     "full_name": "International Conference on Learning Representations",
     "ccf_level": "CCF-A", "caai_level": "CAAI-A",
     "research_areas": ["深度学习", "表示学习", "优化", "生成模型"],
     "acceptance_rate": 0.31, "avg_review_weeks": 8, "review_model": "Double-blind",
     "publication_fee": 0, "is_oa": True, "next_deadline": "2026-09-28", "notification_date": "2027-01-15"},
    # === CCF-B 会议 ===
    {"type": "conference", "abbreviation": "EMNLP",
     "full_name": "Conference on Empirical Methods in Natural Language Processing",
     "ccf_level": "CCF-B", "caai_level": "CAAI-B",
     "research_areas": ["自然语言处理", "文本挖掘", "信息抽取", "情感分析"],
     "acceptance_rate": 0.286, "avg_review_weeks": 8, "review_model": "Double-blind",
     "publication_fee": 0, "is_oa": True, "next_deadline": "2027-06-15", "notification_date": "2027-09-15"},
    {"type": "conference", "abbreviation": "ECCV",
     "full_name": "European Conference on Computer Vision",
     "ccf_level": "CCF-B", "caai_level": "CAAI-B",
     "research_areas": ["计算机视觉", "图像处理", "模式识别"],
     "acceptance_rate": 0.295, "avg_review_weeks": 8, "review_model": "Double-blind",
     "publication_fee": 0, "is_oa": True, "next_deadline": "2027-03-01", "notification_date": "2027-06-15"},
    {"type": "conference", "abbreviation": "NAACL",
     "full_name": "North American Chapter of the ACL",
     "ccf_level": "CCF-B", "caai_level": "CAAI-B",
     "research_areas": ["自然语言处理", "计算语言学", "对话系统"],
     "acceptance_rate": 0.30, "avg_review_weeks": 8, "review_model": "Double-blind",
     "publication_fee": 0, "is_oa": True, "next_deadline": "2027-03-15", "notification_date": "2027-06-15"},
    {"type": "conference", "abbreviation": "ICDM",
     "full_name": "IEEE International Conference on Data Mining",
     "ccf_level": "CCF-B", "caai_level": "CAAI-B",
     "research_areas": ["数据挖掘", "知识发现", "大数据分析", "图挖掘"],
     "acceptance_rate": 0.195, "avg_review_weeks": 8, "review_model": "Double-blind",
     "publication_fee": 0, "is_oa": False, "next_deadline": "2027-06-01", "notification_date": "2027-09-01"},
    # === CCF-A 期刊 ===
    {"type": "journal", "abbreviation": "TPAMI",
     "full_name": "IEEE Transactions on Pattern Analysis and Machine Intelligence",
     "ccf_level": "CCF-A", "caai_level": "CAAI-A",
     "research_areas": ["计算机视觉", "模式识别", "机器学习", "图像分析"],
     "acceptance_rate": 0.12, "avg_review_weeks": 20, "review_model": "Single-blind",
     "publication_fee": 0, "is_oa": False, "next_deadline": "N/A (rolling)", "notification_date": "N/A"},
    {"type": "journal", "abbreviation": "IJCV",
     "full_name": "International Journal of Computer Vision",
     "ccf_level": "CCF-A", "caai_level": "CAAI-A",
     "research_areas": ["计算机视觉", "图像处理", "视觉认知"],
     "acceptance_rate": 0.15, "avg_review_weeks": 16, "review_model": "Single-blind",
     "publication_fee": 0, "is_oa": False, "next_deadline": "N/A (rolling)", "notification_date": "N/A"},
    {"type": "journal", "abbreviation": "JMLR",
     "full_name": "Journal of Machine Learning Research",
     "ccf_level": "CCF-A", "caai_level": "CAAI-A",
     "research_areas": ["机器学习", "统计学习", "优化", "深度学习理论"],
     "acceptance_rate": 0.18, "avg_review_weeks": 18, "review_model": "Single-blind",
     "publication_fee": 0, "is_oa": True, "next_deadline": "N/A (rolling)", "notification_date": "N/A"},
    # === CCF-B 期刊 ===
    {"type": "journal", "abbreviation": "TNNLS",
     "full_name": "IEEE Transactions on Neural Networks and Learning Systems",
     "ccf_level": "CCF-B", "caai_level": "CAAI-B",
     "research_areas": ["神经网络", "深度学习", "学习系统"],
     "acceptance_rate": 0.16, "avg_review_weeks": 14, "review_model": "Single-blind",
     "publication_fee": 0, "is_oa": False, "next_deadline": "N/A (rolling)", "notification_date": "N/A"},
    {"type": "journal", "abbreviation": "PR",
     "full_name": "Pattern Recognition",
     "ccf_level": "CCF-B", "caai_level": "CAAI-B",
     "research_areas": ["模式识别", "计算机视觉", "图像分析"],
     "acceptance_rate": 0.22, "avg_review_weeks": 12, "review_model": "Single-blind",
     "publication_fee": 0, "is_oa": False, "next_deadline": "N/A (rolling)", "notification_date": "N/A"},
    # === CCF-C 会议（保底档） ===
    {"type": "conference", "abbreviation": "ICANN",
     "full_name": "International Conference on Artificial Neural Networks",
     "ccf_level": "CCF-C", "caai_level": "CAAI-C",
     "research_areas": ["神经网络", "深度学习", "机器学习"],
     "acceptance_rate": 0.38, "avg_review_weeks": 6, "review_model": "Double-blind",
     "publication_fee": 0, "is_oa": True, "next_deadline": "2027-05-01", "notification_date": "2027-07-15"},
    {"type": "conference", "abbreviation": "ICPR",
     "full_name": "International Conference on Pattern Recognition",
     "ccf_level": "CCF-C", "caai_level": "CAAI-C",
     "research_areas": ["模式识别", "计算机视觉", "图像处理"],
     "acceptance_rate": 0.42, "avg_review_weeks": 6, "review_model": "Double-blind",
     "publication_fee": 0, "is_oa": False, "next_deadline": "2027-05-15", "notification_date": "2027-08-01"},
]


class VenueKnowledgeBase:
    """会议/期刊知识库"""

    def __init__(self, config: Optional[RetrievalConfig] = None):
        self.config = config or RetrievalConfig()
        self._venues: list[dict] = list(BUILTIN_VENUES)

        # 自动加载抓取到的扩展数据
        try:
            from knowledge.fetched_venues import FETCHED_VENUES
            existing = {v["abbreviation"] for v in self._venues}
            new_count = 0
            for fv in FETCHED_VENUES:
                if fv["abbreviation"] not in existing:
                    self._venues.append(fv)
                    new_count += 1
            if new_count > 0:
                logger.info(f"已自动导入 {new_count} 条扩展数据，总计 {len(self._venues)} 条")
        except ImportError:
            pass

        # 自动加载自定义 JSON
        _custom_path = _data_dir / "custom_venues.json"
        if _custom_path.exists():
            try:
                with open(_custom_path, "r", encoding="utf-8") as _f:
                    custom_list = json.load(_f)
                existing = {v["abbreviation"] for v in self._venues}
                new_count = 0
                for cv in custom_list:
                    if cv["abbreviation"] not in existing:
                        self._venues.append(cv)
                        new_count += 1
                if new_count > 0:
                    logger.info(f"已导入 {new_count} 条自定义数据，总计 {len(self._venues)} 条")
            except Exception as e:
                logger.warning(f"自定义数据加载失败: {e}")

        logger.info(f"已加载 {len(self._venues)} 条会议/期刊记录")

    def search_by_area(self, research_areas: list[str],
                       ccf_levels: Optional[list[str]] = None,
                       venue_type: Optional[str] = None, top_k: int = 150) -> list[dict]:
        results = []
        query_areas = _normalize_areas(research_areas)
        for v in self._venues:
            venue_areas = _normalize_areas(v.get("research_areas", []))
            overlap = venue_areas & query_areas
            if not overlap:
                continue
            if ccf_levels and v.get("ccf_level") not in ccf_levels:
                continue
            if venue_type and v.get("type") != venue_type:
                continue
            results.append({**v, "_bm25_score": len(overlap) / max(len(query_areas), 1)})
        results.sort(key=lambda x: x["_bm25_score"], reverse=True)
        return results[:top_k]

    def search_by_keyword(self, keywords: list[str],
                          ccf_levels: Optional[list[str]] = None, top_k: int = 150) -> list[dict]:
        results = []
        kw_set = _normalize_areas(keywords)
        for v in self._venues:
            text = json.dumps(v, ensure_ascii=False).lower()
            # 同时匹配原词和翻译
            venue_areas = _normalize_areas(v.get("research_areas", []))
            hits = sum(1 for kw in kw_set if kw.lower() in text or kw.lower() in venue_areas)
            if hits == 0:
                continue
            if ccf_levels and v.get("ccf_level") not in ccf_levels:
                continue
            results.append({**v, "_bm25_score": hits / len(kw_set)})
        results.sort(key=lambda x: x["_bm25_score"], reverse=True)
        return results[:top_k]

    def get_by_id(self, venue_id: str) -> Optional[dict]:
        for v in self._venues:
            if v["abbreviation"] == venue_id:
                return v
        return None

    def filter_by_deadline(self, venues: list[dict], max_days: int = 120) -> list[dict]:
        today = datetime.now()
        cutoff = today + timedelta(days=max_days)
        filtered = []
        for v in venues:
            dl = v.get("next_deadline", "")
            if dl == "N/A (rolling)" or not dl:
                filtered.append(v)
                continue
            try:
                dl_date = datetime.strptime(dl, "%Y-%m-%d")
                if dl_date <= cutoff:
                    filtered.append(v)
            except ValueError:
                filtered.append(v)
        return filtered

    def get_all(self) -> list[dict]:
        """获取全部 venue 记录"""
        return self._venues

    def update_dynamic_data(self, venue_id: str, updates: dict):
        for v in self._venues:
            if v["abbreviation"] == venue_id:
                v.update(updates)
                logger.info(f"已更新 {venue_id} 动态数据")
                return


_kb: Optional[VenueKnowledgeBase] = None


def get_knowledge_base(config: Optional[RetrievalConfig] = None) -> VenueKnowledgeBase:
    global _kb
    if _kb is None:
        _kb = VenueKnowledgeBase(config)
    return _kb
