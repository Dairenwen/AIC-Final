# Zhiyan Paper Reading Agent

[![CI](https://github.com/Mau-Q/zhiyan-paper-reading-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Mau-Q/zhiyan-paper-reading-agent/actions/workflows/ci.yml)
[![Source tag](https://img.shields.io/badge/source-v0.6.4-blue.svg)](https://github.com/Mau-Q/zhiyan-paper-reading-agent/tree/v0.6.4)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

面向**单篇文本型计算机论文**的本地精读 Agent。输入本地 PDF 或 arXiv ID，输出
带页码、章节、Chunk 和证据状态的 Markdown/JSON 报告。

它适合需要回答这些问题的场景：

- 论文解决了什么问题，方法的数据流和模块关系是什么？
- 主要实验、指标、基线和结论分别由哪些原文支持？
- 公式、图和表格中有哪些可以独立确认的事实？
- 哪些内容证据不足，只能作为待复核候选，而不能写成确定结论？

项目只做单篇论文的完整、可审计阅读流程，不提供 RAG、多论文比较、前端、数据库、
OCR 或云端服务。

## 快速开始

### 环境要求

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- OpenAI Chat Completions 兼容的文本模型端点
- 可选：兼容图像输入的视觉模型、Poppler、ImageMagick 和本地 Docling 模型

```bash
git clone https://github.com/Mau-Q/zhiyan-paper-reading-agent.git
cd zhiyan-paper-reading-agent
git checkout v0.6.4
uv sync --project agent-core --frozen
cp agent-core/.env.example agent-core/.env
chmod 600 agent-core/.env
```

编辑 `agent-core/.env`，至少配置：

```dotenv
PAPER_READING_SPLITTER_STRATEGY=section_parent_child_v1
PAPER_READING_MODEL_BASE_URL=https://your-endpoint.example/v1
PAPER_READING_MODEL_NAME=qwen3.7-plus
PAPER_READING_ENABLE_THINKING=false
PAPER_READING_MODEL_CREDENTIAL_ENV=MODEL_API_CREDENTIAL
```

API Key 不写入 `.env`，只放在当前 shell：

```bash
read -sr MODEL_API_CREDENTIAL
export MODEL_API_CREDENTIAL
```

当前已验收的本地配置是文本与视觉均使用 `qwen3.7-plus`，并显式关闭思考。网关
仍保持 OpenAI-compatible；更换模型或服务商后应重新进行真实论文验收。

### 阅读本地 PDF

```bash
uv run --project agent-core --frozen python scripts/run_real_pdf_agent.py \
  path/to/paper.pdf \
  --goal "理解研究问题、方法和主要实验" \
  --speed-profile balanced \
  --markdown-output output/report.md \
  --json-output output/report.json \
  --timing-json-output output/timing.json
```

### 阅读公开 arXiv 论文

```bash
uv run --project agent-core --frozen python scripts/run_real_pdf_agent.py \
  --arxiv-id 1706.03762 \
  --goal "解释 Transformer 的核心方法与实验" \
  --speed-profile balanced \
  --markdown-output output/attention.md
```

CLI 会自动读取存在的 `agent-core/.env`。运行产物默认留在本地，不应提交凭据、
原始模型响应或包含私有论文正文的报告。

## 输出内容

| 输出 | 内容 | 用途 |
| --- | --- | --- |
| Markdown | 论文概览、Claim–Evidence、实验、科学对象、限制和流程状态 | 阅读与人工复核 |
| JSON | 完整结构化报告、证据引用、可靠性记录和降级信息 | 自动检查与二次处理 |
| Timing JSON | 各阶段耗时、请求类型、成功状态和 HTTP 状态码 | 性能诊断 |

核心 Claim 会绑定 Evidence；Evidence 保留页码、章节、Chunk ID 和来源信息。
表格数值只有通过独立行列/单元格核验后才能进入 accepted evidence。未确认视觉结果、
低风险待复核内容和可选阶段失败都会显式记录，不会被包装成完整成功。

## 阅读档位

| 档位 | 默认行为 | 适合场景 |
| --- | --- | --- |
| `fast` | `OVERVIEW`，只运行基础阅读 | 初筛论文 |
| `balanced` | `STANDARD`，只运行基础阅读 | 日常阅读，推荐默认使用 |
| `quality` | `DEEP`，启用实验和科学对象分析 | 重要论文、实验与图表复核 |

显式传入的 `--depth`、`--analyze-experiments`、`--analyze-elements` 及其
`--no-*` 形式会覆盖档位默认值。`quality` 会增加模型请求和视觉处理，不建议用于
不需要实验或科学对象分析的初筛任务。

## 执行与可靠性

默认执行模式是 `flow_first`：基础报告完成后，实验、科学对象、问答和解释阶段可
独立运行；可选阶段失败不会抹掉已完成结果。报告中的 `flow_execution` 会记录每个
阶段的状态、降级代码和建议动作。

以下边界始终保持阻断：

- PDF identity 与 source hash 不一致；
- Chunk 或 DocumentIR lineage 无效；
- Claim 引用了未知 Evidence；
- 高风险数值、因果、最佳值或作者归因缺少支持；
- 输出越过单篇论文范围。

需要任一已请求阶段失败都立即终止时，使用：

```bash
--execution-mode strict
```

审阅报告时优先检查：

1. `flow_execution` 是否完成、是否存在 degradation；
2. 核心 Claim 是否绑定有效 Evidence；
3. 表格数值是否来自 accepted check 或已验证 cell fact；
4. `VISION_NOT_CONFIRMED` 和 review candidate 是否仍被明确隔离；
5. timing JSON 的主要耗时是否符合预期。

## 问答、解释和科学对象

可以在同一次阅读中追加一条论文内问题：

```bash
--question "残差连接解决的核心优化问题是什么？"
```

也可以解释论文中的选中文本或一个已经定位的对象，两者不能在同一次运行中同时
使用：

```bash
--explain-text "identity shortcut connection"
```

或：

```bash
--explain-object-id table_p0005_0001
```

科学对象覆盖模式：

- `KEY`：分析排序后的关键 Equation/Figure/Table；
- `SELECTED`：只分析显式 object ID，适合定向复核；
- `COMPREHENSIVE`：尝试全部已定位对象，成本最高。

示例：只复核一张表格。

```bash
uv run --project agent-core --frozen python scripts/run_real_pdf_agent.py \
  path/to/paper.pdf \
  --goal "只核验指定表格中的配置事实" \
  --speed-profile quality \
  --scientific-coverage SELECTED \
  --scientific-object-id table_p0005_0001 \
  --markdown-output output/table-review.md \
  --json-output output/table-review.json
```

问答与解释只使用当前论文上下文，不进行论文外部检索。

## 复杂表格与视觉核验

视觉模型未配置时，Equation/Figure/Table 仍可进行文本分析，但不会获得视觉确认。
如需 Docling TableFormer 候选结构，首次显式下载本地模型：

```bash
uv run --project agent-core --frozen docling-tools models download layout tableformer
export DOCLING_ARTIFACTS_PATH="$HOME/.cache/docling/models"
```

配置视觉模型后，Agent 会结合 caption 锚点、目标区域渲染和候选表格结构核验行、列、
指标及单元格。Docling/PyMuPDF 的结构候选不等于正确答案；只有独立 proof 通过的
comparison 或 cell fact 才能升级为可靠数值证据。

详见[复杂表格提取方案](docs/TABLE_EXTRACTION_SOLUTION_V0_6.md)。

## 当前版本与边界

| 项目 | 状态 |
| --- | --- |
| 稳定源码标签 | `v0.6.4` |
| 默认执行模式 | `flow_first`；可选 `strict` |
| 当前回归 | Agent Core `176/176`；Golden `31/31`；四篇各 `12/12` 不变量 |
| 模型验收 | 文本/视觉 `qwen3.7-plus`，显式关闭思考 |
| CI | macOS/Python 3.13、Windows/Python 3.12 |
| GitHub Release | `v0.5.0`；`v0.6.4` 以源码 tag 交付 |

当前承诺范围：

- 单篇、标准文本型计算机论文；
- macOS 为主要开发与完整真实验收环境；
- Windows 保留 CI 与历史接收证据，但不声称通用 Windows 产品支持；
- 扫描件、OCR、非标准复杂 PDF、多论文比较和产品化能力不在当前范围；
- Fixture/Fake、空结果或未确认视觉内容不能冒充真实成功。

完整限制见[限制说明](docs/LIMITATIONS_V0_4.md)。

## 开发与验证

拉取已通过 CI 的 `main`：

```bash
git switch main
git pull --ff-only origin main
```

文档或不改变运行行为的修改：

```bash
uv run --project agent-core --frozen python scripts/validate_delivery.py --static-only
```

提交运行行为修改前：

```bash
uv run --project agent-core --frozen python scripts/validate_delivery.py
```

只有 Parser、Splitter、Router、Prompt、模型适配、可靠性或科学对象行为变化时，
才需要补对应的真实论文验收。贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)，
安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

## 文档

- 使用：[Quick Start](docs/QUICKSTART_V0_4.md) · [User Guide](docs/USER_GUIDE_V0_4.md)
- 输出与执行：[Output Format](docs/OUTPUT_FORMAT_V0_4.md) · [Execution Modes](docs/EXECUTION_MODES_V0_5.md)
- 设计：[Architecture and Reliability](docs/ARCHITECTURE_AND_RELIABILITY_V0_4.md)
- 质量：[Six-paper Regression Baseline](quality/SIX_PAPER_REGRESSION_BASELINE.md) · [V0.6.4 Proof Recovery](review-evidence/resnet-v0.6.4-qwen3.7-proof-recovery.md)
- 发布：[V0.6.4 Release Notes](docs/RELEASE_NOTES_V0_6_4.md) · [Delivery Acceptance](docs/DELIVERY_ACCEPTANCE_V0_6.md)
- 路线：[Future Improvements](docs/FUTURE_IMPROVEMENTS.md)
- 许可：[MIT License](LICENSE) · [Third-party Notices](THIRD_PARTY_NOTICES.md)
