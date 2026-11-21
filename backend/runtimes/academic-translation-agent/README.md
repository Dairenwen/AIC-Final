# Academic Translation Agent

学术文档翻译工具。默认通过 OpenAI 兼容 API 调度翻译模型，保留论文版式、公式、引用、数值、缩写和方法名；也可以显式切换回 Ollama。

## 目录说明

```text
academic-translation-agent/
├── README.md                    # 本说明
├── translate_pdf.sh             # Linux/macOS 一键启动脚本
├── translate_pdf.ps1            # Windows PowerShell 一键启动脚本
├── agent-core/
│   ├── src/                     # 翻译 Agent、PDF、图像、表格处理实现
│   ├── data/terminology.json    # 内置学术术语
│   ├── outputs/                 # 容器运行时中间输出（可安全清理）
│   └── prompts/                 # 翻译与术语提示词
├── agent-system/
│   ├── backend/                 # 本地 API
│   └── docker/                  # Docker 镜像与启动配置
└── output/pdf/                  # 最终交付 PDF 与任务 JSON
```

## 解压后直接运行

API 模式只需要 Docker Desktop 和 API 配置。脚本会构建并启动容器、执行翻译、将最终文件写入 `output/pdf/`。本地 Ollama 模式才需要安装 Ollama 并准备模型。

在执行脚本前设置：

```text
TRANSLATION_LLM_BACKEND=api
TRANSLATION_API_BASE_URL=https://api.siliconflow.cn/v1
TRANSLATION_API_MODEL=deepseek-ai/DeepSeek-V4-Flash
TRANSLATION_API_KEY=your-key
```

当前 SiliconFlow 默认使用已实测响应更快的 `deepseek-ai/DeepSeek-V4-Flash`。专业翻译可以切换到阿里百炼兼容接口的 `qwen-mt-turbo`；恢复 Ollama 时设置 `TRANSLATION_LLM_BACKEND=ollama`。

### Linux / macOS

```bash
bash ./translate_pdf.sh '/absolute/path/to/paper.pdf'
```

示例：

```bash
bash ./translate_pdf.sh '/Users/drw/Downloads/科研/Molecular Relational Learning/2024-ACL-MolTC_Towards Molecular Relational Modeling In Language Models.pdf'
```

### Windows PowerShell 7+

```powershell
pwsh -ExecutionPolicy Bypass -File .\translate_pdf.ps1 'C:\absolute\path\to\paper.pdf'
```

已在 PowerShell 7 中时：

```powershell
.\translate_pdf.ps1 'C:\absolute\path\to\paper.pdf'
```

## 命令行参数

Linux/macOS 参数放在 PDF 路径之后：

```bash
bash ./translate_pdf.sh paper.pdf [参数]
```

PowerShell 参数使用命名形式：

```powershell
.\translate_pdf.ps1 paper.pdf [-参数 值]
```

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `--source` / `-SourceLang` | `en` | 源语言代码。 |
| `--target` / `-TargetLang` | `zh` | 目标语言代码。 |
| `--no-figures` / `-NoFigures` | 关闭 | 跳过图像与表格视觉覆盖，速度略快。 |
| `--parallel` / `-Parallel` | `2` | 本机翻译并发，范围 1-5；12B 长文推荐 2。 |
| `--timeout` / `-Timeout` | `600` | PDF 翻译硬超时，范围 60-3600 秒。 |
| `--glossary` / `-GlossaryJson` | `{}` | JSON 术语表，例如 `{"MolTC":"MolTC","molecular graph":"分子图"}`。 |
| `--bilingual` / `-Bilingual` | 关闭 | 同时生成原文-译文交替的双语 PDF；可能更大。 |

Linux/macOS 示例：

```bash
bash ./translate_pdf.sh paper.pdf \
  --source en --target zh --parallel 2 \
  --glossary '{"Molecular Relational Learning":"分子关系学习","MolTC":"MolTC"}'
```

PowerShell 示例：

```powershell
.\translate_pdf.ps1 paper.pdf -Parallel 2 -GlossaryJson '{"Molecular Relational Learning":"分子关系学习","MolTC":"MolTC"}'
```

查看 Bash 参数帮助：

```bash
bash ./translate_pdf.sh --help
```

## 输出与限制

成功后终端会打印：

```text
output/pdf/<原文件名>-zh.pdf
output/pdf/<原文件名>-result.json
```

最终 PDF 强制限制为 10 MB。超限、API 失败或超过超时时间时，脚本只保留结果 JSON，不会把不完整 PDF 当成成功产物。

在 Apple M5 Max 上，16 页学术论文使用固定 12B 模型的真实冷启动约 10 分钟。首次模型下载或首次 Docker 构建会额外耗时，但后续运行会复用模型与镜像。

## 清理

以下目录仅含生成物，可随时删除：

```text
agent-core/outputs/
output/pdf/
```
