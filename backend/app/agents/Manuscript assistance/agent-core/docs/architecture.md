# Document Assistant —— 顶层设计文档

## 1. 项目概述

### 1.1 目标
构建一个基于 LangChain + LangGraph 的智能 Agent 工程，当前 Agent 名称为 Document Assistant。系统采用多 Agent 协作架构，核心能力与服务子系统按职责拆分到不同目录中。

### 1.2 核心能力
- **摘要生成**：根据论文整体内容生成结构化摘要
- **引言撰写**：梳理研究背景、动机、贡献点
- **相关工作整理**：文献检索、分类、对比分析
- **方法描述**：技术方案的结构化表达
- **实验设计**：实验设置、结果分析、可视化描述
- **总结展望**：工作总结与未来方向

---

## 2. 系统架构

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

---

## 3. 核心模块设计

### 3.1 编排控制层 (OrchestratorAgent)

**职责**：
- 接收用户指令，解析写作意图
- 将任务分解并分发到对应的子 Agent
- 管理各章节之间的上下文传递与一致性
- 输出质量评估与迭代反馈

**实现方式**：
- 使用 LangChain 的 `AgentExecutor` + 自定义 Router Chain
- 基于 LangGraph 构建有状态的工作流图（StateGraph）
- 支持顺序执行和并行执行两种模式

```python
# 核心状态定义
class PaperState(TypedDict):
    user_input: str                    # 用户原始输入
    paper_topic: str                   # 论文主题
    paper_outline: str                 # 论文大纲
    references: List[Document]         # 参考文献
    sections: Dict[str, str]           # 各章节内容
    feedback: List[str]                # 反馈记录
    current_step: str                  # 当前步骤
    iteration_count: int               # 迭代次数
```

### 3.2 子 Agent 设计

#### 3.2.1 摘要 Agent (AbstractAgent)
| 属性 | 说明 |
|------|------|
| 输入 | 论文主题、各章节摘要/关键点、关键词 |
| 输出 | 结构化摘要（背景-问题-方法-结果-结论） |
| 工具 | 语法检查、词汇润色、字数控制 |
| 策略 | 先生成骨架，再填充细节，最后精炼 |

#### 3.2.2 引言 Agent (IntroductionAgent)
| 属性 | 说明 |
|------|------|
| 输入 | 研究主题、研究动机、贡献点列表 |
| 输出 | 引言文本（背景→问题→现有不足→本文方案→贡献点→结构概览） |
| 工具 | 文献检索、引用格式化、逻辑链校验 |
| 策略 | 漏斗式写作（从宽泛到具体） |

#### 3.2.3 相关工作 Agent (RelatedWorkAgent)
| 属性 | 说明 |
|------|------|
| 输入 | 研究主题、关键词、已有参考文献 |
| 输出 | 分类综述文本、文献对比表 |
| 工具 | 文献检索(Semantic Scholar/arXiv)、RAG检索、对比表生成 |
| 策略 | 先分类分组，再逐组综述，最后指出gap |

#### 3.2.4 方法 Agent (MethodAgent)
| 属性 | 说明 |
|------|------|
| 输入 | 技术方案描述、算法伪代码、模型架构信息 |
| 输出 | 方法章节文本、公式描述、算法流程 |
| 工具 | LaTeX公式格式化、图表描述生成、符号一致性检查 |
| 策略 | Overview → 模块详述 → 整体流程 |

#### 3.2.5 实验 Agent (ExperimentAgent)
| 属性 | 说明 |
|------|------|
| 输入 | 实验设置、数据集信息、实验结果数据 |
| 输出 | 实验章节文本（设置、结果、分析、消融实验） |
| 工具 | 表格生成、数据分析描述、对比分析 |
| 策略 | 设置 → 主实验 → 对比分析 → 消融 → 可视化描述 |

#### 3.2.6 总结展望 Agent (ConclusionAgent)
| 属性 | 说明 |
|------|------|
| 输入 | 各章节核心内容总结、研究贡献 |
| 输出 | 总结段落 + 未来工作方向 |
| 工具 | 全文一致性检查、语言润色 |
| 策略 | 回顾贡献 → 总结发现 → 局限性 → 未来方向 |

### 3.3 工具层 (Tools)

| 工具名称 | 功能 | 实现方式 |
|----------|------|----------|
| `LiteratureSearchTool` | 学术文献检索 | Semantic Scholar API / arXiv API |
| `RAGRetrievalTool` | 基于用户上传文档的检索 | FAISS/Chroma + Embedding |
| `LaTeXFormatterTool` | LaTeX格式化与公式渲染 | 模板 + 正则处理 |
| `GrammarCheckTool` | 语法与学术表达检查 | LLM-based / LanguageTool |
| `CitationTool` | 引用格式生成(BibTeX) | CrossRef API |
| `TranslationTool` | 中英文学术翻译 | LLM-based |
| `OutlineGeneratorTool` | 论文大纲生成 | LLM-based + 模板 |
| `ConsistencyCheckTool` | 全文一致性/逻辑性校验 | LLM-based |
| `WordCountTool` | 字数统计与控制 | Python内置 |
| `TableGeneratorTool` | Markdown/LaTeX表格生成 | 结构化模板 |

### 3.4 知识层 (Knowledge Layer)

```
knowledge/
├── vector_store/          # 向量数据库（用户上传文献的embedding）
├── templates/             # 各章节写作模板
│   ├── abstract_template.txt
│   ├── introduction_template.txt
│   ├── related_work_template.txt
│   ├── method_template.txt
│   ├── experiment_template.txt
│   └── conclusion_template.txt
├── prompts/               # 各Agent的系统提示词
│   ├── orchestrator_prompt.txt
│   ├── abstract_prompt.txt
│   ├── introduction_prompt.txt
│   ├── related_work_prompt.txt
│   ├── method_prompt.txt
│   ├── experiment_prompt.txt
│   └── conclusion_prompt.txt
└── examples/              # 优秀论文范例（用于few-shot）
```

---

## 4. 工作流程设计

### 4.1 主流程 (基于 LangGraph StateGraph)

```
[用户输入] 
    │
    ▼
[意图解析] ──→ 判断是"全文生成"还是"单章节辅助"
    │
    ├── 全文生成模式 ─────────────────────────────┐
    │                                              │
    ▼                                              ▼
[大纲生成] → [引言] → [相关工作] → [方法] → [实验] → [总结] → [摘要]
    │                                              │
    └── 每步完成后 → [质量评估] → 通过/迭代 ────────┘
    │
    ├── 单章节模式 ───────────────────────┐
    │                                      │
    ▼                                      ▼
[章节Agent执行] → [质量评估] → [输出/迭代]
    │
    ▼
[最终输出] → 格式化(Markdown/LaTeX) → 返回用户
```

### 4.2 质量控制循环

每个章节生成后经过质量评估：
1. **结构完整性**：是否覆盖必要要素
2. **逻辑连贯性**：段落间逻辑是否通顺
3. **学术规范性**：表达是否符合学术写作规范
4. **一致性**：与其他章节的术语、符号是否一致
5. **字数控制**：是否在合理范围内

不通过则带反馈重新生成（最多迭代 N 次）。

### 4.3 上下文管理策略

- **全局上下文 (Global Context)**：论文主题、关键词、贡献点、符号表
- **章节上下文 (Section Context)**：当前章节已生成内容、相邻章节摘要
- **长期记忆 (Long-term Memory)**：使用 ConversationBufferMemory + 向量检索
- **上下文压缩**：对长文本使用 summarize chain 压缩后传递

---

## 5. 技术栈

| 层次 | 技术选型 | 说明 |
|------|----------|------|
| 框架 | LangChain + LangGraph | Agent编排与工作流 |
| LLM | GPT-4 / Claude / 通义千问 | 核心生成能力 |
| Embedding | text-embedding-3-small / BGE | 文档向量化 |
| 向量数据库 | FAISS / Chroma | 本地轻量级检索 |
| 文献API | Semantic Scholar / arXiv | 文献检索 |
| 前端(可选) | Gradio / Streamlit | 快速原型交互界面 |
| 后端(可选) | FastAPI | API服务化 |
| 配置管理 | Pydantic + YAML | 参数与提示词管理 |
| 日志 | LangSmith / 本地日志 | 调试与追踪 |

---

## 6. 项目目录结构

```
agent-core/
├── docs/                          # 设计文档
│   └── architecture.md
## 6. 项目目录结构
│   ├── __init__.py
│   ├── main.py                    # 入口
langgraph-agent/
├── README.md                        # 项目说明文档：架构介绍、使用指南、环境配置与运行示例
├── pyproject.toml                   # 项目元数据与依赖管理：适配 uv/poetry 等现代 Python 包管理工具
├── requirements.txt                 # 依赖清单：pip 兼容版依赖声明，作为备选部署方案
├── .env.example                     # 环境变量模板：API 密钥、存储配置、模型参数等敏感项的示例模板
├── .gitignore                       # Git 忽略配置：屏蔽环境变量、缓存、日志等无需版本管理的文件
├── main.py                          # 程序启动入口：提供 CLI 调用、本地调试、完整流程演示入口
├── config/                          # 全局配置模块：统一管理所有配置项与全局常量
│   ├── __init__.py                  # 模块初始化文件
│   ├── settings.py                  # 配置加载核心：基于 pydantic-settings 读取环境变量与运行配置
│   └── constants.py                 # 全局常量：模型名称、流程阈值、状态枚举、工具默认参数等
├── src/                             # 核心源码目录：存放 Agent 全部业务逻辑与功能模块
│   ├── __init__.py                  # 源码包初始化文件
│   ├── agent/                       # LangGraph 核心工作流：图状态定义、节点逻辑、路由边、图编译组装
│   ├── tools/                       # 工具集模块：所有遵循 LangChain 规范的可调用工具，按领域分类管理
│   ├── llm/                         # 模型封装层：对话大模型与嵌入模型的统一封装，屏蔽多厂商 API 差异
│   ├── memory/                      # 记忆模块：会话管理、短期对话历史、长期记忆持久化与检索
│   ├── schemas/                     # 数据结构层：入参出参 DTO、枚举值、工具输入输出 Pydantic 模型
│   └── utils/                       # 通用工具库：日志配置、Token 计数、格式化、自定义异常等公共能力
├── tests/                           # 测试目录：单元测试与集成测试用例
│   ├── __init__.py                  # 测试包初始化文件
│   ├── test_graph_flow.py           # 工作流集成测试：验证 Agent 完整调用链路与分支路由逻辑
│   ├── test_tools.py                # 工具单元测试：校验各工具输入输出正确性与异常处理
│   └── test_llm.py                  # 模型调用测试：验证大模型/嵌入模型封装的可用性
└── assets/                          # 静态资源目录：存放非代码类项目资源
    ├── prompts/                     # 静态提示词资源：yaml/txt 格式的提示词模板文件
    └── examples/                    # 示例数据：测试样例、演示输入、基准用例数据
```
│   │   ├── orchestrator.py
│   │   ├── abstract.py
│   │   ├── introduction.py
│   │   ├── related_work.py
│   │   ├── method.py
│   │   ├── experiment.py
│   │   └── conclusion.py
│   └── utils/                     # 工具函数
│       ├── __init__.py
│       ├── logger.py
│       └── helpers.py
├── templates/                     # 写作模板
├── examples/                      # 示例论文
├── tests/                         # 测试
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## 7. 关键设计决策

### 7.1 为什么用多 Agent 而非单 Agent？

| 维度 | 单Agent | 多Agent(本方案) |
|------|---------|----------------|
| 提示词管理 | 单一超长prompt，容易冲突 | 各Agent独立prompt，职责清晰 |
| 上下文窗口 | 容易超限 | 按需加载，高效利用 |
| 可维护性 | 修改一处影响全局 | 模块化，独立迭代 |
| 质量控制 | 难以针对性评估 | 每步可独立评估反馈 |
| 可扩展性 | 添加功能需重写prompt | 新增Agent即可 |

### 7.2 LangGraph vs AgentExecutor

选择 **LangGraph** 作为核心编排引擎：
- 支持有状态的循环工作流（迭代优化）
- 支持条件分支（根据质量评估决定下一步）
- 支持人机交互节点（用户确认/修改）
- 支持持久化检查点（断点续写）

### 7.3 RAG 策略

- 用户上传的参考文献 → 分块 → Embedding → 存入向量库
- 写作时根据当前章节主题检索相关片段
- 使用 Parent Document Retriever 保持上下文完整性
- Reranking 提升检索精度

---

## 8. 接口设计

### 8.1 用户输入接口

```python
class PaperRequest(BaseModel):
    """用户论文写作请求"""
    topic: str                              # 论文主题
    keywords: List[str]                     # 关键词
    contributions: List[str]                # 贡献点
    target_section: Optional[str] = None    # 目标章节(None=全文)
    references: Optional[List[str]] = None  # 参考文献路径
    language: str = "en"                    # 目标语言
    style: str = "academic"                 # 写作风格
    additional_context: Optional[str] = None # 补充说明
```

### 8.2 输出接口

```python
class PaperResponse(BaseModel):
    """论文生成结果"""
    sections: Dict[str, SectionContent]     # 各章节内容
    outline: str                            # 使用的大纲
    references_used: List[Reference]        # 使用的参考文献
    metadata: GenerationMetadata            # 生成元数据
    suggestions: List[str]                  # 改进建议

class SectionContent(BaseModel):
    title: str
    content: str                            # Markdown/LaTeX格式
    word_count: int
    quality_score: float                    # 质量评分
    iteration_count: int                    # 迭代次数
```

---

## 9. 扩展规划

### Phase 1 — MVP (当前)
- [x] 设计文档
- [ ] 基础框架搭建
- [ ] 编排Agent + 2-3个子Agent
- [ ] 基本的Prompt Engineering
- [ ] CLI交互

### Phase 2 — 增强
- [ ] 全部6个子Agent
- [ ] RAG文献检索集成
- [ ] 质量评估与迭代机制
- [ ] Web UI (Gradio/Streamlit)

### Phase 3 — 完善
- [ ] 多语言支持
- [ ] LaTeX完整输出
- [ ] LangSmith可观测性
- [ ] 用户偏好学习
- [ ] 协作编辑模式

---

## 10. 风险与应对

| 风险 | 影响 | 应对措施 |
|------|------|----------|
| LLM幻觉 | 生成不实内容 | RAG增强 + 事实校验工具 |
| 上下文超限 | 长论文无法完整处理 | 分段处理 + 摘要压缩 |
| 章节不一致 | 术语/符号前后矛盾 | 全局符号表 + 一致性检查 |
| API成本 | 多次迭代费用高 | 设置迭代上限 + 缓存 |
| 学术规范 | 不符合目标期刊格式 | 模板驱动 + 格式后处理 |
