# Flask 后端骨架

## 启动

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
flask --app wsgi run --debug --port 5000
```

首次升级到项目制科研工作区时，执行一次可重复的数据库初始化：

```powershell
flask --app wsgi init-research-workspace
```

该命令创建项目、成员、文档版本和研究产物表，并为已有的 `tasks`、`conversations` 补充项目归属字段。

## 创新点生成 Agent

后端直接运行独立 Agent 仓库 `agent/paper-insight-generate/innovation_agent.py`。可通过环境变量覆盖目录和运行限制：

```env
INNOVATION_AGENT_ROOT=../../../agent/paper-insight-generate
INNOVATION_AGENT_TIMEOUT_SECONDS=1800
INNOVATION_AGENT_MAX_DOCUMENTS=80
```

任务请求示例：

```json
{
  "agent_code": "innovation_point_generation",
  "prompt": "动态 RAG 的可靠性评估与证据更新",
  "innovation_mode": "full",
  "innovation_top_k": 5,
  "innovation_time_range": "2022-2026",
  "innovation_seed_ideas": ["按证据置信度动态更新检索策略"]
}
```

服务会从 `zhiyan.papers` 导出当前平台文献语料，执行趋势分析、研究空白识别、创新点评分和证据绑定，并将 Agent 原始 JSON 路径记录在任务的 `output.artifacts.result_json` 中。

默认数据库地址为 `postgresql+psycopg://zhiyan:zhiyan@localhost:5432/zhiyan`。如果仅检查界面，API 的工作台接口带有演示数据，不依赖数据库连接；`/api/v1/health/ready` 会明确返回数据库是否可用。

## 当前接口

```text
GET  /api/v1/health/live
GET  /api/v1/health/ready
POST /api/v1/auth/login
GET  /api/v1/auth/me
POST /api/v1/auth/logout
POST /api/v1/auth/sms/request
POST /api/v1/auth/sms/login
GET  /api/v1/workspace/summary
GET  /api/v1/agents
GET  /api/v1/agent-teams
GET  /api/v1/tools
GET  /api/v1/skills
GET  /api/v1/knowledge-bases
GET  /api/v1/history
GET  /api/v1/users/me
GET  /api/v1/projects
POST /api/v1/projects
GET  /api/v1/projects/{id}/workspace
POST /api/v1/projects/{id}/documents
PATCH /api/v1/projects/{id}/documents/{document_id}
POST /api/v1/projects/{id}/conversations
GET  /api/v1/conversations/{id}/messages
POST /api/v1/projects/{id}/artifacts/from-task
POST /api/v1/uploads/papers
POST /api/v1/uploads/patents
POST /api/v1/tasks
GET  /api/v1/tasks/{id}
GET  /api/v1/tasks/{id}/events
POST /api/v1/tasks/{id}/patent-selection
GET  /api/v1/tasks/{id}/artifacts/{artifact_kind}
GET  /api/v1/admin/overview
```

除健康检查和登录相关接口外，所有 API 都要求登录。浏览器端使用 HttpOnly 会话 Cookie，写操作还需携带登录响应返回的 CSRF Token。短信接口已保留完整契约，在短信供应商未配置时返回 `SMS_PROVIDER_NOT_CONFIGURED`。

首次使用已有种子用户前，需要初始化密码：

```powershell
flask --app wsgi set-user-password --phone +8613800000000
```

`POST /api/v1/tasks` 和 SSE 任务流使用同一套登录会话鉴权；普通用户只能读取自己的任务、历史和知识库，系统管理员可访问管理概览。

## 论文精读 Agent

将内置论文精读 Agent 同步到数据库：

```powershell
flask --app wsgi sync-builtin-agents
```

客户端先通过 `POST /api/v1/uploads/papers` 上传 PDF，再用 `POST /api/v1/tasks` 创建任务：

```json
{
  "agent_code": "paper_reading",
  "prompt": "理解论文的研究问题、方法、实验、创新与局限",
  "attachment_id": "上传接口返回的 uploadId",
  "attachment": "paper.pdf",
  "speed_profile": "balanced"
}
```

也可省略上传字段并传入 `link`，支持 arXiv URL 或 `arxiv:` 编号。运行目录、`uv` 命令、缓存、超时和上传大小均可通过 `.env.example` 中的 `PAPER_READING_*` / `PAPER_UPLOAD_*` 配置覆盖。

## 专利撰写 Agent

专利 Agent 运行时代码已合并到 `app/agents/patent_drafting/runtime`。首次部署执行：

```powershell
flask --app wsgi init-patent-drafting
python -m playwright install chromium
```

第一阶段生成候选专利点并把任务置为 `WAITING_INPUT`；客户端通过
`POST /api/v1/tasks/{id}/patent-selection` 唯一选择一个候选后，第二阶段继续执行 CNIPA 检索、差异分析、交底书和权利要求撰写。任务主数据保存在 `zhiyan.tasks`，专利运行编号、候选、人工选择、摘要和产物索引保存在 `zhiyan.patent_drafting_runs`。

```env
PATENT_UPLOAD_DIR=uploads/patents
PATENT_DRAFTING_RUNTIME_ROOT=app/agents/patent_drafting/runtime
PATENT_DRAFTING_DATA_DIR=generated/patent_drafting
PATENT_DRAFTING_TIMEOUT_SECONDS=3600
PATENT_DRAFTING_FAKE_MODE=false
PATENT_DRAFTING_ALLOW_FIXTURE_FALLBACK=false
```

正式模式不会自动把 Fixture 当作真实 CNIPA 结果。`PATENT_DRAFTING_FAKE_MODE` 仅用于明确的离线测试。
