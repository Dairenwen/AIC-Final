# 华为 ICT 创新赛（赛题一）改造内容归档

本目录集中存放「智研」项目为参加**第十一届华为 ICT 大赛创新赛赛题一**所做的改动和材料。
这些内容**不属于 AIC 决赛线上部署版本**，与仓库主干（`backend/`、`frontend/`、`scripts/`）严格分开，互不影响。

## 仓库主干与本目录的关系

仓库主干以服务器 `zhiyan.uc1002.xyz` 上实际运行的部署代码为基线（2026-11-12 从服务器拉取）。
ICT 改造版是在更早的一份「原始交付包」上独立演进的，而服务器在交付包之后又做过热更新，所以两边是分叉关系，
不能简单地互相覆盖。整理时借助 `original-source-sha256.json`（原始交付包 969 个文件的哈希）做了三方比对。

| 分类 | 数量 | 处理方式 |
|---|---|---|
| 两边完全一致 | 835 | 只在主干保留一份 |
| ICT 版新增（服务器没有） | 97 | 放入 `overlay/` |
| 仅 ICT 版改动 | 66 | 主干保留服务器版，ICT 版放入 `overlay/` |
| 服务器与 ICT 版各自改过 | 27 | 主干保留服务器版，ICT 版放入 `overlay/` |
| 原始包之外且两边不同（专利工具上游恢复等） | 32 | 主干保留服务器版，ICT 版放入 `overlay/` |
| 仅服务器热更新 | 15 | 主干保留 |
| ICT 版删除、服务器仍在用 | 3 | 主干保留 |

逐文件清单见 [文件差异清单.md](文件差异清单.md)。

## 目录说明

| 路径 | 内容 |
|---|---|
| `overlay/` | ICT 版相对服务器版**新增或不同**的全部源码文件，保持原相对路径。把它覆盖到主干上即可还原 ICT 版源码（另需删除清单 F 类的 3 个文件）。 |
| `ict-changes.patch` | 服务器版 → ICT 版的统一 diff（不含新增文件和锁文件），便于逐行审阅。 |
| `工程改造清单.md` | ICT 赛题一「最小迁移与修复清单」，说明每一项改动的动机。 |
| `README.ict-original.md` | ICT 版的根 README。 |
| `records/` | 参赛记录：官网要求原文与截图、CodeArts 审查记录、证据索引、本机启动与交接、部署包交付说明等。 |
| `文件差异清单.md` | 三方比对的逐文件分类结果。 |

## ICT 版改动要点

为满足赛题要求（华为云部署、CodeArts 智能体/Skills、RDS/DCS）而做、**与 AIC 线上版无关**的部分：

- `overlay/deploy/huawei/`、`overlay/deploy/docker/`：华为云 ECS + RDS PostgreSQL + DCS Redis 的 Docker 化部署、预检、发布事务、数据恢复脚本。
- `overlay/.codeartsdoer/skills/zhiyan-huawei-deploy/`：CodeArts 部署 Skill。
- `overlay/docs/huawei/`：验收清单、修复进度、旧服务器交接清单。
- `overlay/backend/tests/test_huawei_*.py`、`test_docker_*.py`、`test_production_*.py` 等：上述部署脚本的回归用例。
- `overlay/scripts/package_delivery.py`：私有交付包打包脚本。

ICT 版里通用性较强、服务器版没有的功能和修复（是否回迁到主干由后续优化逐项评估，不做整体覆盖）：

- 前端界面整体重做：`styles/` 拆分为 tokens/base/layout/primitives/pages/motion，新增 `AgentHeader`、`AgentIntro`、图标组件和 `catalog/presentation.ts`。
- 后端：`services/shared_cache.py`（Redis 共享缓存）、`services/task_model_runtime.py`（任务默认模型回退）、修改密码接口、SSE 与聊天接口的数据库连接及时释放、DeepSeek 推理模型参数适配。
- 知识库：`pdf_storage.py`、`es_rebuild.py`、`maintenance.py`、语义切块 `chunker.py`、检索后端改造。
- 专利工具按上游固定提交恢复（MIT 许可），恢复 Word 导出。
- Linux 适配：`scripts/dev-server.mjs`、`config.py` 路径处理、`backend/scripts/init_database.py`、依赖锁文件。

## 未入库的材料

以下内容体积过大或含隐私/凭据，**只保留在本机 `Downloads/比赛/ICT`**，不进入 Git：

| 材料 | 位置（相对 ICT 目录） | 未入库原因 |
|---|---|---|
| 私有部署包 `zhiyan-complete-private.tar` | `部署包/2026-11-10/` | 33 GB，含历史用户数据 |
| arXiv 论文 PDF | `newcode/zhiyan_sub/文献/arxiv/` | 31 GB |
| 数据库备份 `zhiyan_2026-10-09_*_pgsql_data.sql` | `newcode/zhiyan_sub/补充/` | 含用户数据 |
| 历史上传/产物、元数据、Milvus 数据、Docling 模型、文献 PPT 工具运行时 | `newcode/zhiyan_sub/补充/` | 约 2.9 GB，运行期数据与模型 |
| RapidOCR 模型 | `newcode/zhiyan_sub/deploy/docker/rapidocr-models/` | 模型权重 |
| 各 `.env` / `.env.production` | `deploy/docker/`、`deploy/huawei/` | 含真实密钥 |
| 虚拟环境、`node_modules`、构建产物、缓存 | 各处 | 可重建 |
