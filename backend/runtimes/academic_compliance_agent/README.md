# 学术合规性校验 Agent

这是一个基于 `LangGraph + LangChain` 思路实现的学术合规性校验 Agent MVP。

当前版本不包含人工复核流程，工作流为：

```text
输入论文 -> 文档解析 -> 加载学术合规规则库 -> 四类大模型辅助并行检测 -> 风险聚合 -> 四类检查结构汇总 -> 报告输出
```

四类专项检测包括：

1. 学术论文规范检查
2. 引用与参考文献核验
3. 图表一致性检查
4. 格式与投稿规范检查

风险等级采用中文五档：`极高`、`高`、`中`、`低`、`极低`。

更完整的流程说明见 `WORKFLOW.md`。

## 运行示例

先进入本文件夹：

```powershell
cd D:\研究生文档-研一下\挑战杯\academic_compliance_agent
```

如需安装依赖：

```powershell
pip install -r requirements.txt
```

如需使用 LangSmith 或大模型 API，请先在 `.env` 中填写密钥。PowerShell 中可以临时加载该文件：

```powershell
Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
  $name, $value = $_ -split '=', 2
  [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), 'Process')
}
```

运行测试：

```powershell
python -m unittest academic_compliance_agent.tests.test_workflow
```

运行样例：

```powershell
python -m academic_compliance_agent.main --input samples/sample_reference_issue.md
```

支持的输入格式：

```text
.md / .txt / .docx / .pdf
```

当前 Agent 已接入 OpenAI-compatible 大模型接口。配置 `.env` 后，大模型会参与：

- 四类专项检测节点的大模型辅助检查
- 汇总四个检查节点结果，生成学术合规性打分、优秀点和修改建议

当前 Agent 也支持 LangGraph 自带记忆功能：

- 短期记忆：使用 LangGraph `checkpointer`，按 `thread_id` 保存同一会话中的运行状态。
- 长期记忆：使用 LangGraph `store`，按 `user_id` 保存用户长期检测画像和最近检测摘要。
- 记忆后端：本地 Docker PostgreSQL。

如果暂时不想调用大模型，可以在 `.env` 中设置：

```env
COMPLIANCE_AGENT_USE_LLM=false
```

也可以检测自己的论文：

```powershell
python -m academic_compliance_agent.main --input "D:\你的论文.docx"
```

检测 PDF：

```powershell
python -m academic_compliance_agent.main --input "D:\你的论文.pdf"
```

默认输出目录：

```text
output/compliance_agent/
```

## PostgreSQL 记忆配置

先在 `.env` 中加入或确认以下配置：

```env
COMPLIANCE_AGENT_MEMORY_ENABLED=true
COMPLIANCE_AGENT_MEMORY_SETUP=true
COMPLIANCE_AGENT_POSTGRES_URI=postgresql://postgres:postgres@localhost:5432/postgres?sslmode=disable
COMPLIANCE_AGENT_USER_ID=student_001
COMPLIANCE_AGENT_THREAD_ID=paper_session_001
```

安装记忆依赖：

```powershell
pip install -r requirements.txt
```

使用记忆运行：

```powershell
python -m academic_compliance_agent.main --input samples/sample_reference_issue.md --user-id student_001 --thread-id paper_session_001
```

同一个 `thread_id` 会复用短期记忆；同一个 `user_id` 会复用长期记忆。换论文但保持 `user_id` 不变，Agent 会读取该用户历史检测摘要。

输出 JSON 中：

- `short_term_memory`：来自 LangGraph checkpointer 的短期会话记忆。
- `memory`：来自 LangGraph store 的长期用户记忆。

## 主要输出

- `*_report.md`：用户可读合规检测报告
- `*_result.json`：结构化检测结果

结构化结果中的 `compliance_summary` 包含：

- `compliance_score`：学术合规性百分制得分
- `excellent_points`：学术合规性的优秀点
- `revision_suggestions`：学术合规性的修改建议
