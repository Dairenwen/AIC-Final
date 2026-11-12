# Innovation Mining Agent 架构

## 目标

**Innovation Mining** 面向本地论文知识库生成候选创新点、研究假设和技术路线，并输出可追溯证据链。精炼后的 `innovations[*].downstream_wengao_inputs` 可直接交给论文写作 Agent `wengao`。

## Workflow

```text
用户输入
  -> LiteratureIntelligenceAgent
  -> TrendAnalysisAgent
  -> GapIdentificationAgent
  -> IdeaGenerationAgent
  -> InnovationEvaluationAgent
  -> RefinementAgent
  -> Top-K 结构化创新点
```

支持模式：

- `full`：完整创新挖掘流程。
- `evaluate`：以种子想法为主做证据检索、评估和精炼。
- `expand`：在种子想法基础上扩展更多创新方向。

## 子 Agent

- `LiteratureIntelligenceAgent`：读取 `data/raw/*.json`，按领域/关键词检索文献，构建主题聚类、知识图谱和引用网络摘要。
- `TrendAnalysisAgent`：按年份和关键词统计研究趋势，给出趋势证据文献。
- `GapIdentificationAgent`：从 limitation/challenge/future work/safety/risk 等信号和稀疏主题桥接中识别研究空白。
- `IdeaGenerationAgent`：使用组合式创新、迁移式创新、TRIZ 矛盾消解、空白填补、假设驱动、问题重构生成候选创新点。
- `InnovationEvaluationAgent`：按新颖性、可行性、影响力、风险四维评分。
- `RefinementAgent`：输出 Top-K 结构化提案、证据链和 `wengao` 对接字段。

## 输出契约

核心 JSON 字段：

- `research_trends`
- `research_gaps`
- `innovations`
- `candidate_innovations`
- `evaluated_innovations`
- `refined_proposals`
- `knowledge_graph_summary`
- `evidence_map`
- `workflow_trace`
- `metadata`

评分公式：

```text
Score = 0.35 * Novelty + 0.25 * Feasibility + 0.30 * Impact - 0.10 * Risk
```

当前实现为本地启发式版本，不强依赖 LangChain/LangGraph、外部文献 API 或 LLM key。后续接入 LangGraph 时，可保留同一状态字段和输出契约，仅替换编排层和工具实现。
