# langgraph-agent

审稿意见回复 Agent 的**独立可交付包**（Python SDK + 最小 CLI）。

只需本目录、PostgreSQL 与 OpenAI 兼容 LLM 端点，即可安装、建表、离线验收与联机接入。**不依赖**任何 Web 服务、Flask 应用或前端工程。

---

## 1. 项目是什么 / 不是什么

| 是 | 不是 |
|----|------|
| 可 `import` 的 Python 包 `langgraph_agent` | Web 站点 / REST API 服务 |
| 四条 LangGraph 工作流（任务初始化 / 分析 / 回复 / 定稿） | Flask / Vue 前后端 |
| SDK 门面 `ReviewAgent` + 最小 CLI `main.py` | 需要浏览器才能验收的系统 |
| 默认 Postgres 适配器 + Checkpointer | 必须挂载外部 backend 才能跑 |

**交付边界**：接收方只需 `langgraph-agent/` + Postgres + LLM。表结构字段与历史 backend 兼容（便于迁移存量库），但本包运行步骤自洽，**不要求**先启动 backend。

---

## 2. 环境要求

| 依赖 | 版本 / 说明 |
|------|-------------|
| Python | **3.12+** |
| PostgreSQL | 14+（业务表、LangGraph Checkpointer 共用同一实例） |
| LLM 端点 | OpenAI-compatible Chat Completions（`base_url` + `api_key`） |
| 操作系统 | Windows / Linux / macOS |

离线验收（import、`demo-offline`、单元测试）**不需要** Postgres 与 LLM 密钥。

---

## 3. 安装

```bash
cd langgraph-agent

python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
# source .venv/bin/activate

pip install -e .
# 开发 / 跑测试
pip install -e ".[dev]"
```

安装后验证：

```bash
python -c "from langgraph_agent import ReviewAgent; print('ok')"
```

---

## 4. 配置

```bash
cp .env.example .env
# 联机时编辑 .env，填入 DATABASE_URL / LLM_* 等
```

### 环境变量一览

| 变量 | 必填 | 说明 | 默认 |
|------|------|------|------|
| `DATABASE_URL` | 联机必填 | PostgreSQL 连接串。推荐 `postgresql://user:pass@host:5432/dbname`；也接受 `postgresql+psycopg://` / `postgres://`，内部会归一化 | （空） |
| `LLM_BASE_URL` | 联机 LLM 必填 | OpenAI 兼容端点，通常以 `/v1` 结尾 | （空） |
| `LLM_API_KEY` | 联机 LLM 必填 | API Key | （空） |
| `MODEL_SPLIT` | 可选 | 拆意见 / 分类模型 | `grok-4.5` |
| `MODEL_ANALYZE` | 可选 | 分析意见模型 | `grok-4.5` |
| `MODEL_PAPER_CARD` | 可选 | 论文卡片模型；空则回退 `MODEL_ANALYZE` | （回退） |
| `MODEL_DRAFT` | 可选 | 回复草稿模型 | `grok-4.5` |
| `LLM_TIMEOUT_SECONDS` | 可选 | split / analyze / reply 共用超时（秒） | `240` |
| `PAPER_CARD_LLM_TIMEOUT_SECONDS` | 可选 | 论文卡片专用超时（秒） | `240`（`.env.example` 示例写 `60`） |
| `SPLIT_MAX_WORKERS` | 可选 | 原文拆分并发 LLM 上限；`1`=串行 | `3` |
| `PAPER_CARD_MAX_WORKERS` | 可选 | 论文卡片子批次并发 | `5` |
| `ANALYSIS_MAX_WORKERS` | 可选 | 批量分析并行上限 | `8` |
| `REPLY_MAX_WORKERS` | 可选 | 批量回复并行上限 | `8` |
| `LLM_EXTRA_BODY` | 可选 | 透传 Chat Completions 的 `extra_body`（JSON 对象字符串） | （空） |
| `MANUSCRIPT_STORAGE_DIR` | 可选 | 慢速模式论文本地存储目录 | `langgraph-agent/.storage/manuscripts` |
| `MANUSCRIPT_MAX_BYTES` | 可选 | 论文文件大小上限（字节） | `20971520`（20MB） |

**`DATABASE_URL` 格式示例**：

```text
postgresql://postgres:postgres@localhost:5432/response_agent
```

代码侧：

- SQLAlchemy 使用 `postgresql+psycopg://...`（`settings.sqlalchemy_url()`）
- LangGraph PostgresSaver 使用原生 libpq `postgresql://...`（`settings.libpq_url()`）

读取配置：

```python
from config.settings import get_settings

settings = get_settings()
# settings.sqlalchemy_url()
# settings.libpq_url()
```

> `config` 位于包根目录（与 `main.py` 同级），**不是** `langgraph_agent` 子模块。在包根运行，或确保 `langgraph-agent/` 在 `PYTHONPATH` 中。editable install（`pip install -e .`）后 `langgraph_agent` 可直接 import；`config` 仍建议在包根执行。

**导出目录说明**：定稿导出文件写入包内固定路径 `.tmp/finalize_exports/`（代码常量，**无**对应环境变量）。勿把该目录当密钥提交。

---

## 5. 数据库初始化

业务表（Workspace / Suggestion / Analysis / Reply / GraphRun 等）与 LangGraph Checkpointer 表共用同一个 PostgreSQL 实例。

```bash
cd langgraph-agent
cp .env.example .env   # 至少填 DATABASE_URL
python scripts/init_db.py
```

`scripts/init_db.py` 会：

1. 执行本包 `migrations/`（Alembic `upgrade head`）创建/升级业务表；
2. 初始化 LangGraph Postgres Checkpointer 所需表（`PostgresSaver.setup()`）。

表结构字段与历史 backend 兼容，可直接指向已有库；全新环境用上述脚本即可自举。

联机演示数据（可选）：

```bash
python scripts/seed_manual.py   # 写入与 sample_task_init.json 对齐的 workspace/意见
```

---

## 6. 离线验收（无密钥、无数据库）

在 `langgraph-agent/` 下、已 `pip install -e ".[dev]"` 后：

```bash
# 1) 包可导入
python -c "from langgraph_agent import ReviewAgent; print('ok')"

# 2) 无密钥干跑：Memory + FakeStores + mock 图
#    路径：start_task_init → WAITING_HUMAN → 自动确认 → WAITING_HUMAN → 自动确认 → SUCCEEDED
python main.py demo-offline
# 期望：打印 AgentResult JSON，最终 status=SUCCEEDED，退出码 0

# 3) 单元测试（跳过需 DB/LLM 的 integration）
pytest -q -m "not integration"

# 或一键离线验收 + 打包风险检查
python scripts/verify_delivery.py --offline
python scripts/pack_check.py
```

关闭自动确认（停在第一次 `WAITING_HUMAN`）：

```bash
python main.py demo-offline --no-auto-approve
```

其他离线 CLI：

```bash
python main.py demo-task-init
python main.py demo-task-init --auto-approve
python main.py demo-task-init --input assets/examples/sample_task_init.json
```

---

## 7. 联机验收（有 DB + LLM）

前提：`.env` 已配置 `DATABASE_URL`、`LLM_BASE_URL`、`LLM_API_KEY`，且数据库已初始化。

```bash
cd langgraph-agent

# 1) 建表 + checkpointer
python scripts/init_db.py

# 2) 真配置启动任务初始化（Postgres stores + Postgres checkpointer）
python main.py demo-task-init --live --auto-approve

# 3) 跨进程续跑示例（需上一步留下的 thread_id / pending 字段）
python main.py resume --live --input assets/examples/sample_resume.json
# 或覆盖 thread_id：
# python main.py resume --live --thread-id "workspace:<uuid>:task:<uuid>" --input path/to/resume.json
```

> CLI 当前子命令：`demo-offline` / `demo-task-init` / `resume`。分析、回复、定稿请用 SDK（见第 8、9 节）。

一键联机验收（缺配置会跳过并 exit 2，**不会**误报成功）：

```bash
python scripts/verify_delivery.py --live
```

通过标准：

- 上述命令退出码 `0`
- `demo-task-init --live` 能返回合法 `AgentResult`（`WAITING_HUMAN` 或 `SUCCEEDED` 均可；失败为 `FAILED` 且 exit≠0）
- `pytest -q -m "not integration"` 全绿

---

## 8. 业务工作流总览

1. 准备业务数据（工作区、审稿意见原文等）写入 Postgres  
2. 调用 SDK 启动某一阶段  
3. 若返回 `WAITING_HUMAN`，用 `pending` 渲染 UI，收集用户输入  
4. 调用 `resume` 继续  
5. 用 `result_refs` / 库表拿到下游 ID，进入下一阶段  

```text
宿主写入：Workspace + ReviewParty + ReviewInput（is_current=true）
        │
        ▼
① start_task_init  ──interrupt──► CONFIRM_SUGGESTIONS / CONFIRM_RELATIONS
        │                         （SLOW 另有 CONFIRM_BASELINE）
        ▼ SUCCEEDED
   result_refs: [{type:"suggestion", id:...}, ...]
        │
        ▼ 对每条 suggestion
② start_analysis ──interrupt──► CONFIRM_CLASSIFICATION（条件触发）
        │                      CONFIRM_MODIFICATION_FACTS（通常必经）
        ▼ SUCCEEDED
   库中：analysis_snapshot + modification_facts
        │
        ▼ 对每个 source（一条意见来源）
③ start_reply ──interrupt──► REVIEW_REPLY_DRAFT（主人工点）
        │   （策略/事实在图内多为自动确认，一般不挂起）
        ▼ SUCCEEDED
   库中：已审核通过的 source reply
        │
        ▼ 全部回复就绪后
④ finalize  ──无 interrupt──► SUCCEEDED / FAILED
   artifacts / .tmp/finalize_exports/ 导出文件
```

### 8.1 宿主 vs 本包职责

| 宿主（整体项目） | 本包（langgraph-agent） |
|------------------|-------------------------|
| 登录鉴权、权限 | 不校验登录 |
| 创建/编辑 Workspace、上传审稿原文、论文 | 通过 stores 读已有数据 |
| UI 展示 `pending`、收集用户编辑 | 产出 `PendingInteraction` |
| 任务队列 / 异步调度（可选） | 默认同步 `invoke` / `resume` |
| 业务侧 run 列表、消息通知 | 可选 RunStore；默认不写 HTTP 轮询协议 |
| 渲染最终导出下载 | Finalize 写文件 + 返回 artifacts |

### 8.2 前置数据（不满足会直接 FAILED）

| 阶段 | 库中至少需要 |
|------|----------------|
| `start_task_init` | `workspaces` 行存在且 `user_id` 匹配；至少一条 `is_current=true` 的 `review_inputs` 及其 `review_parties` |
| `start_analysis` | 对应 `suggestion_id` 已存在（通常来自 TASK_INIT 落库） |
| `start_reply` | 该 suggestion 已有**确认后的分析与 modification facts**；`source_id` 属于该 suggestion |
| `finalize` | 工作区内已有可定稿的已批准回复；否则可能 BLOCKED/FAILED |

本地可用种子脚本：

```bash
python scripts/init_db.py
python scripts/seed_manual.py          # 写入示例 workspace + 审稿意见
python scripts/manual_e2e.py --auto-approve   # 联机整流程
```

## 9. 目录结构

```text
langgraph-agent/
├── README.md                 # 本文件（含完整对接协议）
├── DELIVERY.md               # 给测试方的最短验收清单
├── pyproject.toml
├── requirements.txt
├── .env.example
├── main.py                   # CLI 入口（不启动 HTTP）
├── config/                   # 运行时配置（pydantic-settings）
├── alembic.ini
├── migrations/               # 业务表迁移（本包自带）
├── scripts/
│   ├── init_db.py            # 建业务表 + checkpointer
│   ├── seed_manual.py        # FAST 联机种子
│   ├── seed_manual_slow.py   # SLOW 冒烟种子（PDF+三审稿人）
│   ├── manual_e2e.py         # FAST 联机整流程
│   ├── manual_e2e_slow.py    # SLOW 冒烟整流程
│   ├── manual_reply_only.py  # 仅 REPLY
│   ├── verify_delivery.py    # 一键离线/联机自测
│   ├── pack_check.py         # 打包风险检查
│   └── _bootstrap.py
├── src/langgraph_agent/      # 可安装包
│   ├── agent/                # 四条图 + ReviewAgent 门面
│   ├── tools/                # PDF / 论文证据 / 导出
│   ├── llm/                  # 结构化输出与韧性
│   ├── memory/               # MemorySaver / PostgresSaver
│   ├── schemas/              # 公共 DTO（对接类型定义处）
│   ├── ports/                # Store Protocol
│   ├── adapters/postgres/    # 默认 Postgres 实现
│   └── utils/
├── tests/
└── assets/
    ├── prompts/
    └── examples/             # CLI / SDK 样例 JSON
```

架构示意：

```text
ReviewAgent（SDK 门面）
    │  start_task_init / start_analysis / start_reply / finalize / resume / get_state
    ▼
四条 LangGraph
    ├─ TASK_INIT   WorkspaceTaskGraph
    ├─ ANALYSIS    SuggestionAnalysisGraph
    ├─ REPLY       SourceReplyGraph
    └─ FINALIZE    FinalizeGraph（无 interrupt）
    │
    ├─ ports/                 Protocol
    ├─ adapters/postgres/     默认实现
    ├─ llm/ / tools/ / memory/
```

---

## 10. 接口说明

本包**不启动 HTTP**。对外只有两类接口：

1. **SDK（本包正式交付面）**：`ReviewAgent` 方法 + Pydantic DTO  
2. **宿主 HTTP 对照表（可选）**：历史 monorepo 中 Flask 的 `/api/*` 路由 → 应调用的 SDK / 宿主职责  

若你的宿主仍要暴露 REST，请在宿主层封装；**不要**假设本 zip 内有 Flask。

### 10.1 SDK 公共 API（`langgraph_agent`）

安装后：

```python
from langgraph_agent import (
    ReviewAgent,
    AgentResult,
    AgentStatus,
    TaskInitInput,
    AnalysisInput,
    ReplyInput,
    FinalizeInput,
    ResumeCommand,
    PendingInteraction,
    GraphKind,
)
```

#### 构造

| 方法 | 说明 |
|------|------|
| `ReviewAgent.from_settings()` | 读 `.env` / 环境变量：Postgres stores + Postgres checkpointer |
| `ReviewAgent.from_memory(stores)` | 测试/离线：MemorySaver + 调用方 FakeStores |
| `ReviewAgent(stores=..., checkpointer=...)` | 显式注入 |

#### 启动 / 恢复

| 方法 | 输入类型 | 说明 |
|------|----------|------|
| `start_task_init(input)` | `TaskInitInput` 或 dict | 任务初始化（拆意见 / 关系；SLOW 含论文基线） |
| `start_analysis(input)` | `AnalysisInput` 或 dict | 单条 suggestion 分析 + 修改事实 |
| `start_reply(input)` | `ReplyInput` 或 dict | 单条 source 回复草稿 |
| `finalize(input)` | `FinalizeInput` 或 dict | 定稿校验与导出（**无** interrupt） |
| `resume(thread_id, resume_command, graph_kind=None)` | `ResumeCommand` 或 dict | 恢复 `WAITING_HUMAN` |
| `get_state(thread_id, graph_kind=None)` | — | 读 checkpoint values / pending（调试用） |

所有启动/恢复方法返回 **`AgentResult`**：

| 字段 | 类型 | 含义 |
|------|------|------|
| `status` | `RUNNING` / `WAITING_HUMAN` / `SUCCEEDED` / `FAILED` | 统一状态 |
| `thread_id` | str | checkpoint 线程 ID，resume 必须原样带回 |
| `run_id` | UUID | 本次运行 ID |
| `pending` | `PendingInteraction \| null` | 仅 `WAITING_HUMAN` 时有值 |
| `result_refs` | `{type, id}[]` | 落库产物引用（如 suggestion / analysis / reply_draft） |
| `phase` | str \| null | 图内阶段（如 `CONFIRM_SUGGESTIONS`、`REVIEW_DRAFT`、`BLOCKED`） |
| `error_code` | str \| null | 失败码（如 `AGENT_ERROR`） |
| `artifacts` | object | 附加信息（失败详情、finalize 的 block_list 等） |

#### 输入字段摘要

**TaskInitInput**

| 字段 | 必填 | 说明 |
|------|------|------|
| `workspace_id` | 是 | UUID |
| `user_id` | 是 | 非空字符串 |
| `mode` | 否 | `FAST`（默认）/ `SLOW` |
| `manuscript_version_id` | SLOW 建议必填 | 已解析成功的论文版本 |
| `input_version` | 否 | 不传则由门面生成 |

**AnalysisInput**

| 字段 | 必填 | 说明 |
|------|------|------|
| `workspace_id` / `suggestion_id` / `user_id` | 是 | |
| `mode` | 否 | `FAST` / `SLOW` |
| `manuscript_version_id` | SLOW 建议 | 证据/基线 |
| `input_version` | 否 | 默认读 suggestion 行上的版本 |

**ReplyInput**

| 字段 | 必填 | 说明 |
|------|------|------|
| `workspace_id` / `suggestion_id` / `source_id` / `user_id` | 是 | |
| `input_version` | 否 | 默认读 suggestion |

**FinalizeInput**

| 字段 | 必填 |
|------|------|
| `workspace_id` / `user_id` | 是 |
| `input_version` | 否 |

**ResumeCommand**

```json
{
  "workspace_id": "uuid",
  "thread_id": "与 AgentResult.thread_id 一致",
  "interaction_id": "pending.interaction_id",
  "input_version": "pending.input_version",
  "payload": { }
}
```

四项身份字段必须与当前 `PendingInteraction` 一致，否则图内会报版本/线程不匹配。

#### 最小调用示例

```python
from langgraph_agent import ReviewAgent, AgentStatus, ResumeCommand

agent = ReviewAgent.from_settings()

# 1) 任务初始化
r = agent.start_task_init({
    "workspace_id": workspace_id,
    "user_id": "user-1",
    "mode": "FAST",
})
while r.status is AgentStatus.WAITING_HUMAN and r.pending:
    # 宿主用 r.pending 渲染 UI，收集 payload
    cmd = ResumeCommand(
        workspace_id=r.pending.workspace_id,
        thread_id=r.pending.thread_id,
        interaction_id=r.pending.interaction_id,
        input_version=r.pending.input_version,
        payload={"approved": True},  # 或按 editable_fields 填
    )
    r = agent.resume(r.thread_id, cmd)

# 2) 分析 → 3) 回复 → 4) 定稿（同理）
```

#### 常见 `pending.interaction_type`

| interaction_type | 所属阶段 | 说明 |
|------------------|----------|------|
| `CONFIRM_BASELINE` | TASK_INIT · SLOW | 确认论文信息卡片 |
| `CONFIRM_SUGGESTIONS` | TASK_INIT | 确认拆分后的建议清单 |
| `CONFIRM_RELATIONS` | TASK_INIT | 确认建议间关系 |
| `CONFIRM_CLASSIFICATION` | ANALYSIS | 条件触发：分类需人确认 |
| `CONFIRM_MODIFICATION_FACTS` | ANALYSIS | 确认修改事实 / 动作 |
| `REVIEW_REPLY_DRAFT` | REPLY | 批准或编辑回复草稿 |

`payload` 键与 `pending.editable_fields[].key` 对齐；冒烟脚本会优先用 `field.default`。

---

### 10.2 宿主 HTTP 路由对照（历史 `/api/*`）

下列路由来自原 monorepo **Flask 宿主**（`backend/app/api/*`），前缀均为 **`/api`**。  
**本交付包不实现这些 HTTP 路径**；表中「SDK / 职责」列说明宿主应如何对接本包。

统一响应（宿主惯例）：

```json
// 成功
{ "ok": true, "data": { } }

// 失败
{ "ok": false, "error": { "code": "INVALID_INPUT", "message": "...", "details": {} } }
```

#### 健康检查

| 方法 | 路径 | 宿主职责 / SDK |
|------|------|----------------|
| `GET` | `/api/health` | 宿主自己实现；可顺带 `SELECT 1` 探测 DB |

#### 工作区 Workspace

| 方法 | 路径 | 说明 | 与本包关系 |
|------|------|------|------------|
| `POST` | `/api/workspaces` | 创建工作区 | **宿主写库**（`workspaces`）；本包只读 |
| `GET` | `/api/workspaces` | 工作区列表 | 宿主查询 |
| `GET` | `/api/workspaces/{workspace_id}` | 工作区详情 | 宿主查询 |
| `DELETE` | `/api/workspaces/{workspace_id}` | 删除工作区 | 宿主删除；注意级联业务表 |
| `POST` | `/api/workspaces/{workspace_id}/inputs` | 提交审稿原文 / 启动任务 | 宿主写入 `review_parties` + `review_inputs` 后调用 **`ReviewAgent.start_task_init`**（可异步） |
| `POST` | `/api/workspaces/{workspace_id}/task/resume` | 恢复任务初始化 | **`ReviewAgent.resume`**（`GraphKind.TASK_INIT`） |
| `GET` | `/api/workspaces/{workspace_id}/runs/{run_id}` | 轮询运行状态 | 宿主读 `graph_runs` / 或缓存上次 `AgentResult` |
| `GET` | `/api/workspaces/{workspace_id}/suggestions` | 建议列表 | 宿主读 `suggestions` |
| `GET` | `/api/workspaces/{workspace_id}/settings` | 表达设置 | 宿主读 `workspaces.global_settings` |
| `PATCH` | `/api/workspaces/{workspace_id}/settings` | 更新表达设置 | 宿主写库；影响后续 reply 的 `ResponseSettings` |

#### 分析 Analysis

| 方法 | 路径 | 说明 | 与本包关系 |
|------|------|------|------------|
| `POST` | `/api/workspaces/{workspace_id}/suggestions/{suggestion_id}/analysis` | 启动分析 | **`ReviewAgent.start_analysis`** |
| `POST` | `/api/workspaces/{workspace_id}/suggestions/{suggestion_id}/analysis/resume` | 恢复分析 | **`ReviewAgent.resume`**（ANALYSIS） |
| `GET` | `/api/workspaces/{workspace_id}/suggestions/{suggestion_id}` | 建议详情（含分析快照） | 宿主读 `suggestions` + `analysis_snapshots` + facts |

#### 回复 Reply

| 方法 | 路径 | 说明 | 与本包关系 |
|------|------|------|------------|
| `POST` | `/api/workspaces/{workspace_id}/sources/{source_id}/reply` | 启动回复 | **`ReviewAgent.start_reply`**（body 需带 `suggestion_id`/`user_id`） |
| `POST` | `/api/workspaces/{workspace_id}/sources/{source_id}/reply/resume` | 恢复回复（审核草稿） | **`ReviewAgent.resume`**（REPLY） |
| `GET` | `/api/workspaces/{workspace_id}/sources/{source_id}/reply` | 回复详情 | 宿主读 `source_replies` + `reply_drafts` |
| `PATCH` | `/api/workspaces/{workspace_id}/sources/{source_id}/reply/settings` | 来源级表达设置覆盖 | 宿主写 `suggestion_sources.expression_settings_override` |
| `POST` | `/api/workspaces/{workspace_id}/sources/{source_id}/reply/reopen` | 重开编辑 | 宿主业务；或再 `start_reply` |
| `POST` | `/api/workspaces/{workspace_id}/sources/{source_id}/reply/edit` | 保存编辑草稿 | 宿主写草稿 / 或 resume payload `action=edit` |
| `POST` | `/api/workspaces/{workspace_id}/sources/{source_id}/reply/approve` | 批准草稿 | 等价 resume `action=approve` |

#### 论文 Manuscript（SLOW）

| 方法 | 路径 | 说明 | 与本包关系 |
|------|------|------|------------|
| `POST` | `/api/workspaces/{workspace_id}/manuscripts` | 上传 PDF | **宿主**落盘 + 登记 `manuscript_versions`（`PENDING`→解析→`SUCCEEDED`）；解析可用本包 `tools.pdf_parse` / `tools.paper_card` |
| `GET` | `/api/workspaces/{workspace_id}/manuscripts` | 论文版本列表 | 宿主查询 |
| `GET` | `/api/workspaces/{workspace_id}/manuscripts/{manuscript_version_id}` | 版本详情 / 解析进度 | 宿主查询 `parse_status`、`structure_summary` |
| `POST` | `/api/workspaces/{workspace_id}/manuscripts/{manuscript_version_id}/reparse` | 重新解析 | 宿主触发解析任务 |

SLOW 任务：`parse_status=SUCCEEDED` 后，`start_task_init(mode=SLOW, manuscript_version_id=...)`。

#### 定稿 Finalize

| 方法 | 路径 | 说明 | 与本包关系 |
|------|------|------|------------|
| `POST` | `/api/workspaces/{workspace_id}/finalize` | 启动定稿 | **`ReviewAgent.finalize`** |
| `GET` | `/api/workspaces/{workspace_id}/summary` | 汇总只读 | 宿主读库 / finalize artifacts |
| `GET` | `/api/workspaces/{workspace_id}/exports/latest/files/{file_format}` | 下载导出文件 | 宿主读 `.tmp/finalize_exports/` 或库内导出记录 |

#### 路由 → SDK 速查

```text
POST .../inputs              → 宿主写 ReviewInput + start_task_init
POST .../task/resume         → resume (TASK_INIT)
POST .../suggestions/{id}/analysis         → start_analysis
POST .../suggestions/{id}/analysis/resume  → resume (ANALYSIS)
POST .../sources/{id}/reply                → start_reply
POST .../sources/{id}/reply/resume         → resume (REPLY)
POST .../finalize            → finalize
GET  .../*                   → 多为宿主只读查询，不进图
POST .../manuscripts         → 宿主上传+解析，再 start_task_init(SLOW)
```


