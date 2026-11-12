# Agent Core

`agent-core` 是不依赖持久化的单篇论文精读运行时。

V0.6.4 本地稳定版的正式 CLI 默认 `flow_first`，并保留显式 `strict`；模式语义和
`best_effort` 未实现项见
[Execution Modes V0.5](../docs/EXECUTION_MODES_V0_5.md)。

- `src/agent`：LangGraph 状态、节点和阅读编排；
- `src/paper_context`：文本 PDF 解析、Parse Quality Gate、首页元数据 provenance、
  DocumentIR、本地 Splitter 核心、TableGrid 候选提取和 legacy HTTP 对照适配器；
- `src/reading`：Reading Plan、Context Router、类型化诊断、实验、科学对象、论文内问答、渲染、回放和可靠性；
- `src/llm`：provider-neutral 网关和 OpenAI-compatible 文本/视觉适配器；
- `src/schemas`：Pydantic 运行时模型和公共合同；
- `src/tools`、`src/utils`：端口、结果完整性和 JSON Schema 校验。

从交付包顶层执行 `uv sync --project agent-core --frozen`。正式入口是
`scripts/run_real_pdf_agent.py`：

```bash
uv run --project agent-core --frozen python scripts/run_real_pdf_agent.py --help
```

交付包提供三种进程内 Splitter 策略，但不内置模型、产品 HTTP 服务、数据库、
前端、OCR 或持久聊天。接收方工作副本已有 Windows x64 本地证据，但尚未达到
正式平台支持状态；验证记录见
[本地 Windows 验证记录](../docs/WINDOWS_VALIDATION_REPORT_LOCAL.md)，重构边界见
[Windows 原生运行重构方案](../docs/WINDOWS_NATIVE_REFACTOR_PLAN_V0_4.md)，安装见
[Quick Start](../docs/QUICKSTART_V0_4.md)，运行边界见
[架构与可靠性](../docs/ARCHITECTURE_AND_RELIABILITY_V0_4.md)。

## PyMuPDF + Docling 表格结构

默认锁定依赖包含 PyMuPDF 和 Docling。PyMuPDF 负责 caption/page/hash 锚定，
Docling TableFormer `accurate + cell matching` 作为首选结构候选，匹配失败时
回退 PyMuPDF。模型较大，因此仍由使用者首次显式下载：

```bash
uv sync --project agent-core --frozen
uv run --project agent-core --frozen docling-tools \
  models download layout tableformer
export DOCLING_ARTIFACTS_PATH="$HOME/.cache/docling/models"

uv run --project agent-core --frozen python \
  scripts/benchmark_resnet_tables.py \
  --arxiv-id 1512.03385 \
  --docling-artifacts-path "$DOCLING_ARTIFACTS_PATH"
```

正式 Agent CLI 在配置本地模型目录后，会把候选 TableGrid 加入表格视觉分析
上下文；不配置时不会静默下载模型。TableGrid 仍是
`acceptance_ready=false` 的辅助结构，不是 accepted table checks。

默认安装不会改变第三方许可证义务。PyMuPDF 使用 AGPL 或商业许可证，团队须核对
[第三方声明](../THIRD_PARTY_NOTICES.md)。
