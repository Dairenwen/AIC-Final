# AIC-Final · 智研助研

AIC 国赛决赛项目仓库。「智研」是面向科研人员的一站式助研平台：文献检索、论文精读、创新点生成、稿件辅助、审稿意见回复、专利交底书撰写、学术翻译、学术图表、学术速递等智能体，加上公式识别、文献 PPT 等科研工具，以及个人/平台知识库和可自定义的模型库。

线上地址：<https://zhiyan.uc1002.xyz>

## 仓库结构

| 路径 | 说明 |
|---|---|
| `backend/` | Flask + SQLAlchemy + LangGraph 后端，内嵌知识库运行时（`knowledge_base_runtime/`） |
| `frontend/` | Vue 3 + TypeScript + Vite 前端 |
| `scripts/` | 本机统一启动与可视化检查脚本 |
| `docs/` | 项目文档（部署说明、答辩准备等） |
| `competitions/huawei-ict/` | 为华为 ICT 创新赛所做的改动与材料归档，**与主干分开，不参与线上部署**，详见其 [README](competitions/huawei-ict/README.md) |

主干代码以服务器上实际运行的部署版本为基线。服务器部署版自带的说明原文保留在 [docs/README-服务器部署版原文.md](docs/README-服务器部署版原文.md)。

## 本机运行

```bash
# 后端（Python 3.12）
cd backend
python3.12 -m venv ../.venv && ../.venv/bin/pip install -r requirements.txt \
  -r knowledge_base_runtime/backend/requirements.txt
cp .env.example .env        # 填写 DATABASE_URL、模型 API Key 等
../.venv/bin/flask --app wsgi run --port 5000

# 前端（Node 22）
cd frontend
npm ci && npm run dev
```

## 不入库的内容

密钥与 `.env`、服务器与面板凭据、数据库备份、用户上传与生成产物、模型权重、虚拟环境和构建产物都不进入 Git，规则见 `.gitignore`。
