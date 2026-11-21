# 智研 · 投稿推荐 Agent

基于 LangGraph + LangChain 构建的投稿推荐决策支持系统。

## 概述

面向智研平台 AI 领域科研人员，根据论文主题、方法、实验完整度和创新层次，智能匹配并推荐最合适的会议/期刊投稿目标，输出三级推荐（冲刺/匹配/保底）、风险提示和投稿准备清单。

## 工作流（10 节点）

```
任务接收 → 论文特征提取 → ES BM25 初筛 → Milvus 语义匹配 + bge-reranker 精排 →
引用耦合 → 动态信息聚合 → 竞争态势分析 → 多目标排序 → 投稿清单/策略 → 报告生成
```

## 工具层（12 工具）

| 工具 | 功能 |
|------|------|
| FeatureExtractor | 提取子领域、方法范式、创新层次等 |
| VenueRetriever | ES BM25 粗筛候选 |
| SemanticMatcher | BGE-M3 + bge-reranker 精排 |
| CitationCoupler | 参考文献 venue 分布分析 |
| VenueProfiler | 会议/期刊详细画像 |
| DeadlineTracker | 截稿日期查询与提醒 |
| TrendAnalyzer | 近2年发文趋势分析 |
| CompetitionAnalyzer | 竞争态势评估 |
| AcceptanceEstimator | 录用概率估计 |
| ChecklistBuilder | 投稿准备清单生成 |
| Comparator | 多候选横向对比矩阵 |
| ReportGenerator | 结构化推荐报告 |

## 内置知识库

18+ AI 顶会/顶刊元数据（NeurIPS, ICML, CVPR, ACL, AAAI, IJCAI, ICLR, EMNLP, ECCV, TPAMI, JMLR...）

## 快速开始

```bash
pip install -r requirements.txt
python demo.py
```

```python
import asyncio
from agent import recommend_submission

result = await recommend_submission(
    paper_id="PAPER-001",
    parsed_paper={"title": "...", "abstract": "...", "references": [...]},
    quality_estimate={"experiment_completeness": 0.72, "novelty_level": "substantial"},
    user_preferences={"target_ccf_levels": ["CCF-A", "CCF-B"], "max_review_weeks": 12},
)
print(result["final_report"])
```

## 设计原则

- **精准匹配** — 论文全文多维度语义分析
- **多维评估** — 方向匹配度 + 级别 + Deadline + 接收率 + 审稿周期 + 版面费
- **可解释** — 推荐理由、风险提示、数据来源、置信度
- **动态更新** — Deadline/接收率准实时同步
- **个性化可配置** — 级别区间、审稿周期、OA倾向等
- **生态协同** — 上游接论文精读/合规性审查 Agent
