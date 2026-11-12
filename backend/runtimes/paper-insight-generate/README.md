# Papers with Code 知识库爬虫

这套脚本按三步组织：

1. `crawl_papers.py`：从 Papers with Code 的 `/tasks` 自动发现任务页，再爬取论文详情页，生成“每个子领域一个 JSON”。
2. `split_by_venue_year.py`：读取第一步 JSON，按 `conference + year` 重新划分。
3. `download_pdfs.py`：读取 JSON 中的 `pdf_url`，按 `会议_年份_序号.pdf` 下载论文。
4. `paper_lifecycle.py`：批量回写生命周期时间戳。
5. `crawler_task_runs.py`：记录每日自动爬虫任务运行结果。

## 安装依赖

```bash
pip install -r requirements.txt
```

可选：如果网页结构变化导致静态请求拿不到字段，可以安装 Playwright：

```bash
pip install playwright
playwright install chromium
```

## 目录结构

```text
pwc_kb_crawler/
  config.example.yaml
  requirements.txt
  crawl_papers.py
  split_by_venue_year.py
  download_pdfs.py
  paper_lifecycle.py
  crawler_task_runs.py
  README.md
```

## 第一步：爬论文详情页

先复制配置：

```bash
cp config.example.yaml config.yaml
```

编辑 `config.yaml`，默认已经配置为从 `https://paperswithcode.co/tasks` 自动发现任务页。脚本会优先使用 `https://paperswithcode.co/api/v1/areas/with-tasks/` 和 `/api/v1/tasks/{slug}/papers` 这类公开接口，比解析网页更稳定。你可以通过 `max_tasks` 控制最多爬多少个任务，通过 `max_pages_per_task` 控制每个任务页最多翻几页。

运行：

```bash
python crawl_papers.py --config config.yaml --out data/raw
```

也可以不改配置，直接指定入口：

```bash
python crawl_papers.py --config config.example.yaml --tasks-url https://paperswithcode.co/tasks --max-tasks 20 --out data/raw
```

输出示例：

```text
data/raw/agents.json
data/raw/language_modeling.json
data/raw/image_classification.json
```

每条论文字段包括：

```json
{
  "source_url": "https://paperswithcode.com/paper/...",
  "arxiv_id": "2602.11409",
  "title": "...",
  "authors": ["..."],
  "abstract": "...",
  "conference": "ICML",
  "year": 2026,
  "pdf_url": "https://arxiv.org/pdf/2602.11409.pdf",
  "arxiv_url": "https://arxiv.org/abs/2602.11409",
  "github_url": "https://github.com/...",
  "project_url": "...",
  "tasks": ["Agents"],
  "methods": ["Transformer"],
  "citations": 0,
  "related_papers": []
}
```

## 第二步：按会议和年份划分

```bash
python split_by_venue_year.py --input data/raw --out data/by_venue_year
```

输出示例：

```text
data/by_venue_year/ICML_2026.json
data/by_venue_year/arXiv_2026.json
```

## 第三步：下载 PDF

```bash
python download_pdfs.py --input data/by_venue_year --out pdfs
```

命名格式：

```text
ICML_2026_001.pdf
ICML_2026_002.pdf
arXiv_2026_001.pdf
```

## 可选：导入 PostgreSQL 并同步 Elasticsearch

先安装依赖：

```bash
pip install -r requirements.txt
```

然后把 `data/raw` 里的论文 JSON 导入 PostgreSQL。默认会连接本机 `postgres` 数据库，并创建 `papers` 表：

```bash
python import_to_postgres.py --input data/raw --host localhost --port 5432 --database postgres --user postgres
```

入库规则：

- 直接使用 `item["id"]` 作为 PostgreSQL 主键。
- 将 `item["key_words"]` 原样写入 `keywords` 数组字段。
- 将 `item["Author"]` 写入 `author` 字段。
- `related_papers`、`github_url`、`pdf_url` 等字段按名称映射入库。
- 使用 `INSERT ... ON CONFLICT (id) DO NOTHING`，重复 `id` 会跳过。
- 只有成功插入的新记录会异步同步到 Elasticsearch 的 `papers_idx` 索引。

如果需要在命令行提供密码，可以先设置环境变量：

```powershell
$env:PGPASSWORD="你的PostgreSQL密码"
python import_to_postgres.py --input data/raw --database postgres --user postgres
```

默认 Elasticsearch 地址是 `https://localhost:9200`，索引名是 `papers_idx`。如果使用 Elasticsearch 自动生成的本地证书，可以传入 `http_ca.crt`。也可以手动指定：

```bash
python import_to_postgres.py --input data/raw --database postgres --user postgres --es-url https://localhost:9200 --es-index papers_idx --es-ca-cert C:\Users\Administrator\Desktop\elasticsearch-9.4.3\config\certs\http_ca.crt
```

如果 Elasticsearch 里已有旧数据或重复数据，可以以 PostgreSQL 的唯一主表为准重建索引：

```bash
python import_to_postgres.py --input data/raw --database postgres --user postgres --es-url https://localhost:9200 --es-index papers_idx --es-ca-cert C:\Users\Administrator\Desktop\elasticsearch-9.4.3\config\certs\http_ca.crt --rebuild-es
```

## 可选：刷新元数据时间戳

如果爬虫 JSON 里的元数据有更新，可以直接按 `id` 更新 PostgreSQL 中已有记录，并给整批元数据写入同一个 `metadata_updated_at` 时间戳。脚本会默认按 `id` 去重，保留第一次出现的元数据：

```bash
python update_metadata_timestamps.py --input data/raw --database postgres --user postgres
```

每条记录的 `raw` JSON 中也会写入同一个 `metadata_timestamp` 字段，便于追踪这批元数据是哪次刷新写入的。

## 本人负责模块：时间戳与自动爬虫

### 元数据更新时间戳

- `metadata_updated_at`：PostgreSQL 中已有元数据的最后更新时间，由 `update_metadata_timestamps.py` 批量刷新写入。
- `metadata_timestamp`：同步写入 `raw` JSON，用于标记这批元数据对应的刷新批次。
- `upload_time`、`parse_finish_time`、`update_time`、`last_refresh_time`、`delete_time` 等生命周期字段已纳入爬虫产物和 PostgreSQL 表结构。
- 新爬取记录会自动写入 `upload_time`、`parse_finish_time`、`update_time`、`metadata_updated_at`；刷新脚本会更新 `metadata_updated_at`、`last_refresh_time`、`update_time`。
- `split_by_venue_year.py --update-db` 会回写 `chunk_gen_time`；`download_pdfs.py --update-db` 会回写 `parse_finish_time`。

### 自动爬虫任务

- 自动爬虫由 `run_pwc_daily.ps1` 统一调度，先执行 `crawl_papers.py`，再执行 `import_to_postgres.py`。
- 任务开始和结束时间记录在 `logs/pwc_daily_*.log` 中，便于回溯每轮爬取与入库过程。
- 每轮任务会额外写入 `logs/pwc_daily_*.json`，包含 `task_start_time`、`task_end_time`、`add_paper_count`、`skip_paper_count`、`exception_create_time`。

也可以运行本地控制台：

```bash
python app_server.py
```

打开 `http://127.0.0.1:8765`，填写 PostgreSQL 和 Elasticsearch 连接信息后点击“入库并同步 ES”。

## 注意

- 请控制 `request_delay_seconds`，避免给目标网站造成压力。
- Papers with Code 页面可能变动，脚本里已经尽量使用多种选择器和 JSON-LD/meta 信息兜底。
- 如果只想爬你飞书文档中列出的固定论文链接，可以把这些链接放到 `config.yaml` 的 `paper_urls` 字段。

## 创新点生成 Agent（chuangx）

已新增本地可运行的创新点生成 Agent，入口为：

```bash
python innovation_agent.py --domain "多模态大模型安全检测" --keyword multimodal --keyword LLM --keyword safety --seed-idea "小样本场景下的鲁棒性评估" --top-k 5 --out data/innovation_runs --corpus data/raw
```

本地控制台也已增加“创新点生成 Agent”面板：

```bash
python app_server.py
```

打开 `http://127.0.0.1:8765` 后可填写研究领域、关键词、种子想法、模式和约束 JSON，一键生成 Top-K 创新点。

实现模块：

- `chuangx/`：创新点生成 Agent 包。
- `chuangx/agents/`：文献情报、趋势分析、空白识别、创新生成、创新评估、创新精炼 6 个子 Agent。
- `chuangx/tools/`：本地文献检索、知识图谱、趋势统计、新颖性检测、可行性评估、影响力/风险估计、证据绑定等工具。
- `knowledge/innovation_methods/`、`knowledge/evaluation_rubrics/`、`prompts/`：方法库、评分标准和提示词契约。

输出 JSON 默认写入 `data/innovation_runs/`，核心字段包括 `research_trends`、`research_gaps`、`innovations`、`candidate_innovations`、`evaluated_innovations`、`evidence_map`、`workflow_trace`、`metadata`。每个创新点包含四维评分、证据链，以及可直接传给论文写作 Agent `wengao` 的 `downstream_wengao_inputs`。
