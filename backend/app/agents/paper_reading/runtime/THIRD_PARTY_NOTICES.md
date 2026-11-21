# 第三方声明

本项目代码使用 [MIT License](LICENSE)。运行依赖在
`agent-core/pyproject.toml` 中声明，并由 `agent-core/uv.lock` 固定解析结果。
各依赖继续适用其上游许可证。

## 主要 Python 依赖

- [LangGraph](https://github.com/langchain-ai/langgraph)：Agent 工作流编排；
- [Pydantic](https://github.com/pydantic/pydantic)：结构化合同与校验；
- [pypdf](https://github.com/py-pdf/pypdf)：文本型 PDF 解析；
- [httpx](https://github.com/encode/httpx)：OpenAI-compatible 模型请求；
- [jsonschema](https://github.com/python-jsonschema/jsonschema)：JSON Schema 校验。

## 表格结构依赖

- [PyMuPDF](https://pymupdf.readthedocs.io/en/latest/about.html)：用于原生 PDF
  的身份、caption、页码、几何锚定、候选表格提取与 Docling 失败回退，属于默认
  锁定依赖。
  PyMuPDF 由上游以 GNU AGPL 或 Artifex 商业许可证提供；本仓库的 MIT License
  不替代其许可证。团队在把该能力用于分发、服务或正式环境前，应独立确认
  所选许可证与使用方式相容。
- [Docling](https://github.com/docling-project/docling)：默认安装代码依赖，
  使用 TableFormer `accurate + cell matching` 恢复复杂表格结构。Docling
  代码与 `docling-ibm-models` 代码采用 MIT License；具体模型继续适用其上游
  模型许可。模型由用户显式下载，不随仓库分发。

## 可选外部工具

视觉分析可调用 Poppler 的 `pdftoppm` / `pdftotext` 与 ImageMagick。它们不随
本仓库或 Release ZIP 分发，用户应从各自官方来源安装，并遵守对应许可证。

## 外部模型服务

文本模型和视觉模型通过 OpenAI-compatible 接口调用。模型、服务条款、数据处理
方式、计费和区域合规由用户选择的服务商负责。本仓库不包含模型权重、服务凭据或
用户请求记录。

## 论文与验收材料

公开论文仍归原作者及其许可条款约束。仓库仅保留脱敏的结构化验收报告，不复制
原始论文 PDF。`review-evidence/` 中的论文来源和内容哈希用于可追溯验证。
