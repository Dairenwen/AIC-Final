# 知研助研 Web 系统 Demo V1.2

本目录是系统初始可运行框架，后端已调整为 Flask，业务数据库目标为 PostgreSQL。前端按照 `前端设计.pdf` 实现：深墨绿色导航、荧光黄绿色品牌点缀、近白工作区、细边框和紧凑科研工作台布局。

## 快速启动

### 1. 启动 PostgreSQL 和 Redis（可选）

```powershell
docker compose up -d postgres redis
```

### 2. 启动 Flask

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
flask --app wsgi run --debug --port 5000
```

### 3. 启动 Vue

```powershell
cd frontend
npm install
npm run dev
```

访问 `http://localhost:5173`，使用手机号和密码登录。默认演示账号为 `13800000000`，密码为 `Zhiyan@2026`。登录后可从“我的智能体”进入论文精读工作台；上传 PDF 或添加 arXiv 链接即可创建任务，并通过 SSE 查看执行进度。

依赖安装完成后，也可以在项目根目录执行统一启动命令：

```powershell
node .\scripts\dev-server.mjs
```

科研技能库导入

```powershell
cd backend
flask --app wsgi sync-skills
```

该命令读取爬取结果，下载可用来源链接中的完整技能文件，并将内容幂等写入 `zhiyan.skills.definition_json`。科研技能库中的技能详情页会显示文件目录与完整文本；来源链接失效或缺失的条目会保留原始爬取描述并标明下载状态。

统一启动脚本会随 Flask 一起启动 `backend/knowledge_base_runtime` 中的内置知识库服务，不再依赖相邻的 `Web System/zhiyan` 目录或固定的 `8768` 端口。整个知识库管理平台仅向 `system_admin` 开放，入口位于管理员登录后的左侧导航栏，普通用户不可见且不能直接访问相关 UI、静态资源或 API。

内置知识库运行时使用 `backend/.env` 中的 `DATABASE_URL`。主系统表位于 PostgreSQL 的 `zhiyan` schema，知识库表位于 `knowledge_base` schema；知识库 API 服务账号表命名为 `service_accounts`，不会与主系统身份表 `zhiyan.users` 冲突。切片、检索和 QA 模型参数也统一配置在该 `.env` 文件中。

若后续仍需从旧独立数据库导入数据，可使用原交付目录中的幂等迁移脚本：

```powershell
cd "..\zhiyan"
.\.venv\Scripts\python.exe infrastructure\scripts\merge_into_demov15.py
```

## 公式图片转 LaTeX

“科研工具集”中的公式识别工作台调用 `小组传输文件/戴rw/formula-image-to-latex/recognize.py`，图片经格式和大小校验后按用户临时隔离，推理结束即删除。首次使用前需在工具目录执行一次原始启动脚本，以创建专用环境并下载 UniMERNet 权重：

```powershell
cd "..\..\小组传输文件\戴rw\formula-image-to-latex"
PowerShell -ExecutionPolicy Bypass -File .\run.ps1 ".\unimernet\asset\test_imgs\0000001.png"
```

随后在 Web 后端执行 `flask --app wsgi sync-builtin-tools` 同步科研工具目录。运行时位置、设备、超时与上传限制可通过 `backend/.env.example` 中的 `FORMULA_*` 配置覆盖。

## 论文精读 Agent

论文精读服务调用已合并到系统目录 `backend/app/agents/paper_reading/runtime` 的 `0.6.4` 正式工作流。首次运行前，在 Web 后端目录执行：

```powershell
cd backend
flask --app wsgi init-paper-reading
```

该命令会创建 `paper_reading_runs` 表并将 `paper_reading` 智能体同步到数据库。Agent 依赖由系统内运行时的 `agent-core/uv.lock` 锁定，后端通过 `uv run --frozen` 执行；常用配置项见 `backend/.env.example`：

```text
PAPER_READING_RUNTIME_ROOT=app/agents/paper_reading/runtime
PAPER_READING_UV_EXECUTABLE=uv
PAPER_READING_UV_CACHE_DIR=../../../tmp/uv-paper-reading-cache
PAPER_READING_TIMEOUT_SECONDS=3600
PAPER_READING_MODEL_TIMEOUT_SECONDS=180
PAPER_UPLOAD_MAX_BYTES=52428800
```

论文来源支持最大 50 MB 的文本型 PDF 和 arXiv URL/编号。上传文件按用户隔离保存；报告、科学对象、实验复现、可靠性审计、阶段状态和耗时诊断写入 PostgreSQL，任务和历史记录只对任务所有者及系统管理员可见。

## 目录边界

```text
demov1.2/
├─ frontend/       Vue 3 + TypeScript + Vite 用户端和管理端
├─ backend/        Flask + SQLAlchemy + Flask-Migrate API
├─ docker-compose.yml
└─ README.md
```

当前版本已接入会话鉴权、用户权限隔离和论文精读 Agent；生产部署仍应补充短信供应商、异步任务队列、对象存储以及反向代理层的上传限制。

## 学术合规性检测 Agent

学术合规工作台调用同级 `agent/academic_compliance_agent` 的正式工作流，支持 MD、TXT、DOCX 和 PDF 稿件。上传文件、任务记录和报告产物均按 Web 用户隔离；在任务中选择个人中心已验证的模型后，服务仅通过子进程环境变量传递模型地址、模型名和 API Key。

```text
COMPLIANCE_AGENT_ROOT=../../../agent/academic_compliance_agent
COMPLIANCE_AGENT_TIMEOUT_SECONDS=1800
COMPLIANCE_AGENT_USE_LLM=true
COMPLIANCE_AGENT_MEMORY_ENABLED=false
COMPLIANCE_UPLOAD_MAX_BYTES=52428800
```

默认关闭 Agent 自有的长期记忆，由 Web 系统负责用户和任务边界。模型不可用时，Agent 会使用确定性规则引擎完成论文规范、引用、图表一致性和投稿格式四类检查。

文稿辅助优先使用任务选择的已验证个人模型，其次使用平台模型。平台模型不可用时，默认生成带明确提示的确定性结构化底稿，不伪装成模型生成内容；可通过 `MANUSCRIPT_ALLOW_DETERMINISTIC_FALLBACK=false` 关闭降级。

创新点生成在 Windows 上使用独立短输出目录，默认位置为 `backend/generated/ip`，可通过 `INNOVATION_DATA_DIR` 覆盖，以避免时间戳和研究问题文件名触发长路径限制。

## 学术翻译 Agent

学术翻译工作台调用同级 `agent/academic-translation-agentv3/academic-translation-agent` 的 `agent-core` 正式 CLI，支持 MD、TXT、DOCX 和 PDF 文档。任务按用户隔离上传和输出，结果页提供译文预览、质量检查、术语表和文件下载。

```text
TRANSLATION_AGENT_ROOT=../../../agent/academic-translation-agentv3/academic-translation-agent
TRANSLATION_AGENT_TIMEOUT_SECONDS=3600
TRANSLATION_HEARTBEAT_SECONDS=30
TRANSLATION_OLLAMA_BASE_URL=http://127.0.0.1:11434
TRANSLATION_OLLAMA_MODEL=translategemma:12b
TRANSLATION_PDF2ZH_COMMAND=
```

首次使用前需要在本机 Ollama 中准备固定模型 `translategemma:12b`。若启用“保留 PDF 原版式”，还需配置可用的 `pdf2zh` 命令；阅读级翻译默认生成 Markdown、DOCX 和质量报告，不依赖版式渲染器。

“我的智能体”目录会检查运行时文件和关键模型端点，并展示“可用 / 降级可用 / 依赖异常”。检查结果默认缓存 30 秒，可通过 `AGENT_READINESS_CACHE_SECONDS` 和 `AGENT_READINESS_CONNECT_TIMEOUT_SECONDS` 调整。

## 专利撰写 Agent

专利撰写工作台使用已合并到 Web 后端的 `backend/app/agents/patent_drafting/runtime`，支持文本说明以及 MD、TXT、DOCX、PPTX、PDF 和常见源代码材料。工作流先生成候选专利点并等待人工唯一选择，再继续完成 CNIPA 检索、差异分析、技术交底书、权利要求草案、确定性校验和证据复核包。

```powershell
cd backend
pip install -r requirements.txt
python -m playwright install chromium
flask --app wsgi init-patent-drafting
```

任务、候选、人工选择、运行摘要和产物索引写入 PostgreSQL；上传材料与生成文件按用户和任务隔离。Fake/Fixture 仅用于离线验证，不代表真实检索成功或法律结论。

## 绘图创作 Agent

绘图创作工作台使用已合并到 Web 后端的 `backend/app/agents/academic_figure/runtime`。它支持 CSV、TSV、Excel、JSON 数据，PDF、DOCX、TXT、Markdown、LaTeX 上下文，以及常见图片草图；可生成折线图、柱状图、散点图、箱线图、热力图、流程图和图片拼版。

首次使用前执行：

```powershell
cd backend
pip install -r requirements.txt
flask --app wsgi init-academic-figure
```

主要配置项如下：

```text
FIGURE_UPLOAD_DIR=uploads/figures
FIGURE_UPLOAD_MAX_BYTES=52428800
ACADEMIC_FIGURE_RUNTIME_ROOT=app/agents/academic_figure/runtime
ACADEMIC_FIGURE_DATA_DIR=generated/academic_figure
ACADEMIC_FIGURE_TIMEOUT_SECONDS=1800
```

工作台通过 `POST /api/v1/uploads/figures` 分组上传数据、上下文和图片，再通过统一的 `POST /api/v1/tasks` 创建任务。结果包含中英文 PNG/SVG/PDF、Python/R/LaTeX/Mermaid 代码、双语图注、规范化 CSV、FigureSpec、质量报告与产物清单。在线模式使用平台模型或个人中心已验证模型；离线确定性模式会在界面明确标识，不冒充模型规划。

## 学术速递 Agent

学术速递工作台使用已合并到 `backend/app/agents/arxiv_daily/runtime` 的 arXivDaily 抓取与标准化代码。首页选择“学术速递”提交问题后，会创建统一任务并跳转到每日论文流工作台；页面保留原 Agent 的 CS 分类浏览、同步统计、双语论文卡片、摘要展开和原版 PDF 阅读布局。

首次使用前执行：

```powershell
cd backend
pip install -r requirements.txt
flask --app wsgi init-arxiv-daily
```

主要配置项如下：

```text
ARXIV_DAILY_RUNTIME_ROOT=app/agents/arxiv_daily/runtime
ARXIV_DAILY_CACHE_TTL_SECONDS=3600
ARXIV_DAILY_TIMEOUT_SECONDS=120
ARXIV_DAILY_PDF_CACHE_DIR=generated/arxiv_daily_pdfs
ARXIV_DAILY_PDF_MAX_BYTES=52428800
```

分类、论文卡片、同步时间和告警写入 PostgreSQL 的 `zhiyan.arxiv_daily_runs`。一小时内的同分类请求复用数据库快照；手动同步失败时可回退到最近一次成功数据并明确提示。`GET /api/v1/academic-daily/pdf` 只接受 arXiv 白名单主机的 `/pdf/` 地址，缓存并返回源站原版 PDF，不使用 HTML、截图或 AI 摘要伪造文档。
