# 学术合规性校验 Agent 交付说明

## 一、Agent 简介

学术合规性校验 Agent 是一个面向助研场景的论文提交前合规预检模块。它用于帮助用户在论文提交、项目申报、竞赛材料整理或学位论文初审前，自动检查论文中可能存在的学术规范风险，并给出结构化检测结果和后续修改建议。

本 Agent 基于 `LangChain + LangGraph` 实现：

- `LangGraph` 负责 Agent 工作流编排，包括文档解析、规则加载、四类检查节点并行执行、风险聚合、总结生成和记忆写入。
- `LangChain` 负责工具封装，为后续接入更复杂的 Agent 工具调用机制保留扩展接口。
- 大模型用于四个检查节点的辅助判断，并用于最终合规总结生成。
- PostgreSQL 用作 LangGraph 记忆后端，支持短期记忆和长期记忆。

当前 Agent 包含四类合规检测：

1. 学术论文规范检查
2. 引用与参考文献核验
3. 图表一致性检查
4. 格式与投稿规范检查

每个检查节点都会先执行规则检测，再调用大模型辅助生成该模块的结构化检查结果。最终总结节点会汇总四个检查节点的大模型检查结构，生成：

- 学术合规性打分，百分制
- 学术合规性的优秀点
- 学术合规性的修改建议

Agent 输出两类结果：

- Markdown 报告：面向用户阅读。
- JSON 结构化结果：面向总项目后端、前端展示和数据库存储。

## 二、部署文档

### 1. 目录位置

本 Agent 作为独立模块交付，目录为：

```text
D:\研究生文档-研一下\挑战杯\academic_compliance_agent
```

建议总项目负责人直接接收整个 `academic_compliance_agent` 文件夹。

### 2. 环境要求

建议使用 Python 3.10 及以上版本。当前本地测试环境为：

```text
conda 环境：cs599-project
Python 路径：D:\StudyTool\anaconda3\envs\cs599-project\python.exe
```

进入项目目录：

```powershell
cd "D:\研究生文档-研一下\挑战杯\academic_compliance_agent"
```

安装依赖：

```powershell
pip install -r requirements.txt
```

核心依赖包括：

```text
langchain
langgraph
langgraph-checkpoint-postgres
pyyaml
pydantic
fastapi
langsmith
pypdf
```

### 3. 环境变量配置

项目根目录下提供：

```text
.env.example
```

正式运行时需要配置 `.env`。主要配置包括大模型接口、输出目录、规则集和记忆配置。

大模型配置示例：

```env
OPENAI_API_KEY=你的大模型API密钥
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen-plus
COMPLIANCE_AGENT_USE_LLM=true
COMPLIANCE_AGENT_LLM_TEMPERATURE=0.1
COMPLIANCE_AGENT_LLM_TIMEOUT=60
```

如果暂时不调用大模型，可以设置：

```env
COMPLIANCE_AGENT_USE_LLM=false
```

### 4. PostgreSQL 记忆配置

本 Agent 使用 LangGraph 自带记忆功能：

- 短期记忆：`PostgresSaver` checkpointer，按 `thread_id` 保存同一会话状态。
- 长期记忆：`PostgresStore` store，按 `user_id` 保存用户长期检测画像和最近检测摘要。

如果使用本地 Docker PostgreSQL，需要确认容器已启动。例如当前测试容器：

```text
容器名：academic-postgres
镜像：pgvector/pgvector:pg16
端口：5432
数据库用户：agent
数据库密码：agent123
数据库名：compliance_memory
```

`.env` 中记忆配置示例：

```env
COMPLIANCE_AGENT_MEMORY_ENABLED=true
COMPLIANCE_AGENT_MEMORY_SETUP=true
COMPLIANCE_AGENT_POSTGRES_URI=postgresql://agent:agent123@127.0.0.1:5432/compliance_memory?sslmode=disable&gssencmode=disable
COMPLIANCE_AGENT_USER_ID=student_001
COMPLIANCE_AGENT_THREAD_ID=paper_session_001
```

如果不需要记忆功能，可以设置：

```env
COMPLIANCE_AGENT_MEMORY_ENABLED=false
```

### 5. 命令行运行

基础运行：

```powershell
python -m academic_compliance_agent.main --input samples/sample_reference_issue.md
```

带用户记忆运行：

```powershell
python -m academic_compliance_agent.main --input samples/sample_reference_issue.md --user-id student_001 --thread-id paper_session_001
```

检测自己的论文：

```powershell
python -m academic_compliance_agent.main --input "D:\你的论文.docx" --user-id student_001 --thread-id paper_session_001
```

检测 PDF：

```powershell
python -m academic_compliance_agent.main --input "D:\你的论文.pdf" --user-id student_001 --thread-id paper_session_001
```

支持的输入格式：

```text
.md / .txt / .docx / .pdf
```

### 6. 总项目集成方式

推荐总项目后端直接以 Python 模块方式调用：

```python
from academic_compliance_agent.app.graph.workflow import run_compliance_workflow

result = run_compliance_workflow({
    "user_id": "student_001",
    "thread_id": "paper_session_001",
    "task_type": "paper_precheck",
    "target_rule_set": "default",
    "files": [
        {
            "file_type": "manuscript",
            "path": "上传后的论文文件路径"
        }
    ]
})
```

总项目主要使用以下字段：

```python
result["final_report"]
result["structured_output"]
result["structured_output"]["compliance_summary"]
result["structured_output"]["module_check_results"]
result["structured_output"]["risks"]
result["structured_output"]["memory"]
result["structured_output"]["short_term_memory"]
```

其中 `compliance_summary` 是最终汇总结果，结构如下：

```json
{
  "compliance_score": 86,
  "excellent_points": ["..."],
  "revision_suggestions": ["..."],
  "summary": "..."
}
```

## 三、测试样例

### 测试 1：单元测试

目的：验证基础工作流、四类检测节点、报告生成和结构化输出是否正常。

命令：

```powershell
python -m unittest academic_compliance_agent.tests.test_workflow
```

期望结果：

```text
Ran 2 tests
OK
```

### 测试 2：基础全流程测试

目的：验证文档解析、规则加载、四类检查、风险聚合、总结生成和报告输出是否跑通。

命令：

```powershell
python -m academic_compliance_agent.main --input samples/sample_reference_issue.md --user-id student_001 --thread-id paper_session_001
```

期望结果：

```text
Academic compliance check completed.
Report: ...
JSON: ...
```

生成文件位于：

```text
output/compliance_agent/
```

JSON 中应包含：

```json
{
  "summary": {},
  "compliance_summary": {},
  "module_check_results": {},
  "risks": [],
  "suggestions": [],
  "memory": {},
  "short_term_memory": {}
}
```

### 测试 3：短期记忆测试

目的：验证同一个 `thread_id` 下 LangGraph checkpointer 是否恢复上一轮运行状态。

第一次运行：

```powershell
python -m academic_compliance_agent.main --input samples/sample_reference_issue.md --user-id student_001 --thread-id paper_session_001
```

第二次运行，同一个 `thread_id`：

```powershell
python -m academic_compliance_agent.main --input samples/sample_reference_issue.md --user-id student_001 --thread-id paper_session_001
```

第二次报告中应出现：

```text
短期记忆：已恢复上一轮状态
```

JSON 中应出现：

```json
"short_term_memory": {
  "enabled": true,
  "previous_task_id": "...",
  "previous_compliance_summary": {},
  "previous_risk_summary": {}
}
```

### 测试 4：长期记忆测试

目的：验证同一个 `user_id` 下 LangGraph store 是否保存并读取用户长期检测记录。

第一次运行：

```powershell
python -m academic_compliance_agent.main --input samples/sample_reference_issue.md --user-id student_001 --thread-id paper_session_001
```

第二次运行，换 `thread_id`，保持同一个 `user_id`：

```powershell
python -m academic_compliance_agent.main --input samples/sample_reference_issue.md --user-id student_001 --thread-id paper_session_002
```

第二次 JSON 中应出现：

```json
"memory": {
  "enabled": true,
  "user_id": "student_001",
  "profile": {
    "user_id": "student_001",
    "run_count": 1,
    "last_compliance_score": 86
  },
  "recent_runs": [...]
}
```

### 测试 5：关闭记忆测试

目的：验证关闭记忆后 Agent 仍能正常运行。

临时关闭：

```powershell
$env:COMPLIANCE_AGENT_MEMORY_ENABLED="false"
python -m academic_compliance_agent.main --input samples/sample_reference_issue.md --user-id student_001 --thread-id no_memory_test
```

报告中应出现：

```text
短期记忆：未恢复历史状态
长期记忆：未启用
```

### 测试 6：数据库连接测试

目的：确认本地 Docker PostgreSQL 可以连接。

命令：

```powershell
python -c "import psycopg; conn=psycopg.connect('postgresql://agent:agent123@127.0.0.1:5432/compliance_memory?sslmode=disable&gssencmode=disable'); print(conn.execute('select 1').fetchone())"
```

期望结果：

```text
(1,)
```

## 四、流程图

### 1. Agent 总体流程图

```mermaid
flowchart TD
    A["输入论文文件"] --> B["文档解析"]
    B --> C["加载学术合规规则库"]
    C --> M1["读取 LangGraph 长期记忆"]

    M1 --> D1["学术论文规范检查节点"]
    M1 --> D2["引用与参考文献核验节点"]
    M1 --> D3["图表一致性检查节点"]
    M1 --> D4["格式与投稿规范检查节点"]

    D1 --> E1["规则检测 + 大模型辅助检查"]
    D2 --> E2["规则检测 + 大模型辅助检查"]
    D3 --> E3["规则检测 + 大模型辅助检查"]
    D4 --> E4["规则检测 + 大模型辅助检查"]

    E1 --> F["风险聚合"]
    E2 --> F
    E3 --> F
    E4 --> F

    F --> G["汇总四个检查结构"]
    G --> H["生成学术合规性总结"]
    H --> I["写入 LangGraph 长期记忆"]
    I --> J["输出 Markdown 报告与 JSON 结果"]
```

### 2. 四类检查节点内部流程

```mermaid
flowchart TD
    A["模块输入：解析后的论文 + 规则库 + 记忆信息"] --> B["规则检测"]
    B --> C["生成规则风险项"]
    C --> D["调用大模型辅助检查"]
    D --> E["生成模块检查结构"]
    E --> F["模块百分制得分"]
    E --> G["模块优秀点"]
    E --> H["模块风险项"]
    E --> I["模块修改建议"]
    E --> J["模块总结"]
```

### 3. 记忆机制流程图

```mermaid
flowchart TD
    A["用户发起检测任务"] --> B["传入 user_id 和 thread_id"]
    B --> C["LangGraph Checkpointer 读取短期记忆"]
    B --> D["LangGraph Store 读取长期记忆"]
    C --> E["恢复同一 thread_id 的上一轮状态"]
    D --> F["读取同一 user_id 的历史检测画像"]
    E --> G["执行 Agent 检测流程"]
    F --> G
    G --> H["生成合规总结"]
    H --> I["Checkpointer 保存本轮图状态"]
    H --> J["Store 写入用户长期记忆"]
```

### 4. 总项目集成流程图

```mermaid
flowchart LR
    A["总项目：用户上传论文"] --> B["保存论文文件"]
    B --> C["调用 run_compliance_workflow"]
    C --> D["学术合规性校验 Agent"]
    D --> E["返回 structured_output"]
    D --> F["返回 final_report"]
    E --> G["后端入库"]
    E --> H["前端展示合规评分、优秀点、修改建议"]
    F --> I["前端展示完整 Markdown 报告"]
```

## 五、交付建议

交付给总项目负责人时，建议说明：

```text
该 Agent 已完成独立模块开发，支持命令行运行和 Python 函数集成。它基于 LangGraph 编排工作流，使用 LangChain 封装工具，四个检查节点均接入大模型辅助检查，并支持 PostgreSQL 记忆机制。总项目只需要传入论文文件路径、user_id 和 thread_id，即可获得 Markdown 报告和 JSON 结构化合规结果。
```

正式提交时应注意：

- `.env.example` 可以提交。
- `.env` 如果包含真实大模型 API Key，不建议公开提交。
- PostgreSQL 密码和模型密钥应由部署环境单独配置。
