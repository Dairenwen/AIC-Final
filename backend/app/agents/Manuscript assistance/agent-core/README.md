# Document Assistant

基于 **LangChain + LangGraph** 的智能 Agent 工程，当前 Agent 名称为 Document Assistant。

## 系统架构

```
用户输入 → 编排Agent(Orchestrator) → 子Agent协作 → 质量评估 → 输出
                                          │
          ┌───────────────────────────────┼───────────────────────────────┐
          │         │           │         │         │           │         │
       摘要Agent  引言Agent  相关工作Agent  方法Agent  实验Agent  总结Agent
          │         │           │         │         │           │         │
          └─────────┴───────────┴─────────┴─────────┴───────────┘
                                    │
                    工具层(文献检索/RAG/LaTeX/语法检查)
```

## 功能特性

- 📝 支持论文六大核心章节的自动生成
- 🔄 基于 LangGraph 的有状态工作流，支持迭代优化
- 📚 RAG 文献检索增强，基于上传文献生成内容
- ✅ 自动质量评估和一致性检查
- 🌐 中英文双语支持
- 🛠️ 丰富的工具集（文献检索、LaTeX格式化、语法检查等）

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入你的 API Key
```

### 3. 运行

```bash
# 交互模式
python -m src.main --interactive

# 命令行模式
python -m src.main -i "写一篇关于多模态学习的论文" -t "多模态学习" -k "多模态,特征融合,注意力机制" -l zh
```

## 项目结构

```
langgraph-agent/
├─ agent-core/          # Agent 核心逻辑、tools、workflow、prompt、tests
├─ agent-system/        # 这个子系统
│  ├─ backend/          # 后端 API / service
│  ├─ frontend/         # 前端界面
│  ├─ docker/           # 部署文件
│  └─ README.md
├─ shared/              # 可选，共享 schema / config / utils
└─ docs/
```

## 使用示例

### 生成完整论文
```python
from src.main import run_paper_agent
import asyncio

result = asyncio.run(run_paper_agent(
    user_input="基于图神经网络的社交网络推荐系统",
    topic="Graph Neural Network based Social Recommendation",
    keywords=["graph neural network", "social network", "recommendation"],
    contributions=[
        "提出一种新的图神经网络架构用于社交推荐",
        "设计了社交关系感知的注意力机制",
        "在三个公开数据集上验证了方法的有效性",
    ],
    language="en",
))
print(result)
```

### 生成单个章节
```python
result = asyncio.run(run_paper_agent(
    user_input="帮我写相关工作部分",
    topic="多模态情感分析",
    keywords=["multimodal", "sentiment analysis", "fusion"],
    target_section="related_work",
    language="en",
))
```

## 配置说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| LLM_MODEL | gpt-4 | 使用的大模型 |
| EMBEDDING_MODEL | text-embedding-3-small | 向量化模型 |
| VECTOR_STORE_TYPE | faiss | 向量库类型 |
| max_iterations | 3 | 单章节最大迭代次数 |
| quality_threshold | 0.7 | 质量评分通过阈值 |

## 技术栈

- **LangChain** — Agent框架与工具链
- **LangGraph** — 有状态工作流编排
- **FAISS** — 向量相似度检索
- **OpenAI API** — 大模型推理
- **Semantic Scholar / arXiv API** — 文献检索

## 开发计划

- [x] 架构设计
- [x] 核心骨架代码
- [ ] 完善各Agent的Prompt Engineering
- [ ] RAG检索集成与调优
- [ ] Web UI（Gradio）
- [ ] 质量评估迭代机制
- [ ] LangSmith可观测性集成

## License

MIT
