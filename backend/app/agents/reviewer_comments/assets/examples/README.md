# 示例资源说明

本目录存放 CLI / SDK 演示用 JSON 与可选离线素材。

## 样例 JSON

| 文件 | 用途 | 对应 CLI |
|------|------|----------|
| `sample_task_init.json` | `TaskInitInput`：启动任务初始化 | `python main.py demo-task-init`（默认输入） |
| `sample_resume.json` | `ResumeCommand`：人工确认后续跑 | `python main.py resume --input ...` |

### sample_task_init.json 字段

| 字段 | 说明 |
|------|------|
| `workspace_id` | 工作区 UUID |
| `user_id` | 操作者标识（非空字符串） |
| `mode` | `FAST` 或 `SLOW` |
| `manuscript_version_id` | 可选；SLOW 模式论文版本 |
| `input_version` | 可选；幂等版本串 |

### sample_resume.json 字段

| 字段 | 说明 |
|------|------|
| `workspace_id` | 须与 pending 一致 |
| `thread_id` | checkpoint 线程 ID（见 README 第 10 节约定） |
| `interaction_id` | 当前 pending 的交互 ID |
| `input_version` | 与挂起时一致 |
| `payload` | 按 `editable_fields` 填写，演示常用 `{"approved": true}` |

> 离线 mock 每次运行会生成新的 `interaction_id` / `thread_id`。`sample_resume.json` 中的 ID 仅作字段形状参考；**跨进程 offline resume 不可用**（MemorySaver）。联机续跑请用 `--live`，并把文件中的 ID 换成真实 `pending` 值。

自定义输入：

```bash
python main.py demo-task-init --input assets/examples/sample_task_init.json
python main.py demo-offline --input path/to/your_task_init.json
python main.py resume --live --thread-id "workspace:...:task:..." --input path/to/resume.json
```

## 其他约定

- **不要**把含敏感稿件的真实 PDF 提交进仓库。
- 单测优先使用合成 Markdown / 内存 dict，避免依赖真实 PDF 与外网 LLM。
- 若需本地手工验证 PDF 解析，可把样例放在本目录，并在本地忽略大文件。

## 相关工具入口

| 能力 | 模块 |
|------|------|
| PDF → 章节结构 | `langgraph_agent.tools.pdf_parse.parse_pdf` |
| 规则/LLM 卡片 | `langgraph_agent.tools.paper_card` |
| 证据路由 | `langgraph_agent.tools.paper_evidence` |
| 导出 Markdown/Word/Excel | `langgraph_agent.tools.export_files` |
