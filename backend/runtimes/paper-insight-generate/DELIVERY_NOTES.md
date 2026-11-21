# 交付版本说明

交付日期：2026-07-16

## 本人负责模块

### 元数据更新时间戳

- `crawl_papers.py`：爬取产物写入 `metadata_updated_at`。
- `import_to_postgres.py`：PostgreSQL 表结构补充时间戳字段，新入库记录写入生命周期时间。
- `update_metadata_timestamps.py`：按 `id` 批量刷新已有记录的 `metadata_updated_at`，并在 `raw` JSON 中写入 `metadata_timestamp`。
- `paper_lifecycle.py`：统一维护 `upload_time`、`parse_finish_time`、`update_time`、`last_refresh_time`、`delete_time`、`chunk_gen_time`、`vector_index_time` 等生命周期字段。
- `split_by_venue_year.py --update-db`：拆分结果完成后可回写 `chunk_gen_time`。
- `download_pdfs.py --update-db`：PDF 下载/解析阶段可回写 `parse_finish_time`。

### 自动爬虫任务

- `run_pwc_daily.ps1`：自动执行 Papers with Code 爬取与 PostgreSQL 导入，并生成本轮日志和结构化摘要。
- `crawler_task_runs.py`：写入 `crawler_task_runs` 表，记录 `task_start_time`、`task_end_time`、`add_paper_count`、`skip_paper_count`、`exception_create_time`、退出码、日志文件和摘要 JSON。
- `web/` 与 `app_server.py`：本地控制台支持触发导入、刷新元数据时间戳和查看运行状态。

## 验证情况

- 已执行 Python 编译检查：`crawler_task_runs.py`、`paper_lifecycle.py`、`import_to_postgres.py`、`update_metadata_timestamps.py`、`split_by_venue_year.py`、`download_pdfs.py`、`app_server.py` 均通过。
- 已执行 PowerShell 脚本解析检查：`run_pwc_daily.ps1` 通过。
- 上一轮实际运行日志显示：爬取与 PostgreSQL 导入成功，新增 65 条，跳过重复 10153 条。
- 已修复自动任务记录参数问题：无异常时不再向 `crawler_task_runs.py` 传入空的 `--exception-create-time` 参数。

## 交付包说明

交付包仅包含代码、配置、前端页面和说明文档，不包含 `.venv`、`.git`、`__pycache__`、`data`、`logs` 等运行产物。

## 2026-07-20 补充：创新点生成 Agent

本次补充完成 `chuangx` 创新点生成 Agent：

- 新增 `innovation_agent.py` 命令行入口。
- 新增 `chuangx/` 包，包含 6 个子 Agent：文献情报、趋势分析、空白识别、创新生成、创新评估、创新精炼。
- 新增本地工具层：文献检索、主题聚类、知识图谱、引用网络、趋势统计、RAG 检索、新颖性检测、可行性评估、影响力/风险估计、跨域迁移、证据绑定。
- 新增 `knowledge/innovation_methods/`、`knowledge/evaluation_rubrics/`、`prompts/`。
- `app_server.py` 新增 `POST /api/run/innovation-agent`。
- `web/index.html`、`web/app.js`、`web/styles.css` 新增页面控制区和 Top-K 结果渲染。

已验证：

```bash
python -m py_compile innovation_agent.py app_server.py chuangx/**/*.py
python innovation_agent.py --domain "多模态大模型安全检测" --keyword multimodal --keyword LLM --keyword safety --keyword robustness --seed-idea "小样本场景下的鲁棒性评估" --top-k 5 --out data/innovation_runs --corpus data/raw
```

样例输出已生成在 `data/innovation_runs/20260720T104358Z_innovation-d57c55da92.json`，包含 8 个趋势、8 个研究空白、5 个精炼创新点，并通过断言检查：每个创新点都有 evidence、四维 scores 和 `downstream_wengao_inputs`。
