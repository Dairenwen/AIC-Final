# 慢速模式冒烟评测集（SLOW smoke）

用于本地复现 **SLOW** 工作流：论文基线 → 拆意见 → 分析 → 回复 → 定稿闸门。

## 固定内容

| 文件 | 说明 |
|---|---|
| `attention_is_all_you_need.pdf` | Transformer 论文 PDF（从本机桌面复制的复现副本） |
| `review_comments.txt` | 三位审稿人意见（冒烟用，非完整学术评审） |
| `sample_task_init_slow.json` | `start_task_init` 输入模板 |

## 默认身份

- `workspace_id` = `00000000-0000-4000-8000-000000000010`（与 FAST demo 隔离）
- `user_id` = `demo-user-slow`
- `mode` = `SLOW`

## 前置检查

1. **Docker Desktop 必须启动**（Postgres 在本机容器里；未启动会出现 `connection timeout expired`）
2. 建议安装 PDF 依赖（可选，不装也能用最小结构冒烟）：

```powershell
pip install pymupdf
# 可选更完整解析
pip install pymupdf4llm
```

## 一键步骤

在 `langgraph-agent` 目录、已配置 `.env` 的 venv 中：

```powershell
# 先确认 Docker / 数据库可连
python -c "from langgraph_agent.adapters.postgres.db import create_session_factory; s=create_session_factory()(); print(s.execute(__import__('sqlalchemy').text('select 1')).scalar())"

python scripts/init_db.py
python scripts/seed_manual_slow.py
python scripts/manual_e2e_slow.py --auto-approve
```

可选：

```powershell
# 只用指定 PDF（默认用本目录副本）
python scripts/seed_manual_slow.py --pdf "C:\Users\stf\Desktop\1706.03762v7.pdf"

# 只冒烟到 task_init / analysis / reply
python scripts/manual_e2e_slow.py --auto-approve --stop-after task_init
```

## 冒烟范围说明

- **PDF**：只解析前若干页，写入精简 `structure_summary`（不追求完整结构化）。
- **审稿意见**：三位审稿人文本入库存档；TASK_INIT 会拆分，但不保证拆分质量。
- **ANALYSIS / REPLY**：默认只跑**第一条** suggestion 的分析与回复。
- **FINALIZE**：因其它来源未回复，预期 `phase=BLOCKED` + `MISSING_REPLY`（闸门正确即通过）。
