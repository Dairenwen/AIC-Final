# Innovation Mining LangGraph Agent

基于 LangChain + LangGraph 工程规范整理的智能 Agent 项目。当前核心 Agent 名称为 **Innovation Mining**，Python 包名统一为 `innovation_mining`。

## Project Layout

```text
langgraph-agent/
├─ agent-core/          # Agent 核心逻辑、tools、workflow、prompt、tests
│  ├─ main.py           # Innovation Mining CLI 入口
│  ├─ src/
│  │  └─ innovation_mining/
│  └─ assets/
│     ├─ prompts/
│     ├─ knowledge/
│     └─ examples/
├─ agent-system/        # Agent 系统子项目
│  ├─ backend/          # 后端 API / service
│  ├─ frontend/         # 前端界面
│  ├─ docker/           # 部署文件
│  └─ README.md
├─ shared/              # 共享 schema / config / utils
├─ config/              # 全局配置模块
├─ tests/               # 工程级测试
├─ docs/                # 项目文档
├─ main.py              # 项目根启动入口
├─ pyproject.toml
├─ requirements.txt
└─ .env.example
```

## Run Innovation Mining

```bash
cd langgraph-agent
python main.py --domain "多模态大模型安全检测" --keyword multimodal --keyword LLM --keyword safety --seed-idea "小样本场景下的鲁棒性评估" --top-k 5 --out data/innovation_runs --corpus data/raw
```

也可以直接运行核心入口：

```bash
python agent-core/main.py --domain "多模态大模型安全检测" --top-k 5
```

## Run Agent System

```bash
cd langgraph-agent
python agent-system/backend/app_server.py
```

启动后打开 `http://127.0.0.1:8765`。

## Core Modules

- `agent-core/src/innovation_mining/agents/`：文献情报、趋势分析、空白识别、创新生成、创新评估、创新精炼 6 个子 Agent。
- `agent-core/src/innovation_mining/tools/`：本地文献检索、知识图谱、趋势统计、新颖性检测、可行性评估、影响力/风险估计、证据绑定等工具。
- `agent-core/assets/prompts/`：静态提示词资源。
- `agent-core/assets/knowledge/`：创新方法库和评估标准。
- `agent-system/backend/`：本地 API 服务。
- `agent-system/frontend/`：本地控制台页面。

## Output Contract

Innovation Mining 输出 JSON 默认写入 `data/innovation_runs/`，核心字段包括 `research_trends`、`research_gaps`、`innovations`、`candidate_innovations`、`evaluated_innovations`、`evidence_map`、`workflow_trace`、`metadata`。每个创新点包含四维评分、证据链，以及可直接传给论文写作 Agent `wengao` 的 `downstream_wengao_inputs`。
