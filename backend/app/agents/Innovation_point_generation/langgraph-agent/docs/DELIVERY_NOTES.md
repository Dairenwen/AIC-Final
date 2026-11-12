# 交付版本说明

## 2026-07-22 架构调整

- 核心 Agent 名称统一为 **Innovation Mining**。
- 核心包已迁移为 `agent-core/src/innovation_mining/`。
- CLI 入口已迁移为 `agent-core/main.py`，项目根保留 `main.py` 作为统一启动入口。
- 后端服务已迁移为 `agent-system/backend/app_server.py`。
- 前端页面已迁移为 `agent-system/frontend/`。
- 提示词、方法库、评分标准已迁移为 `agent-core/assets/`。
- 示例输出已迁移为 `agent-core/assets/examples/`。

## 运行入口

```bash
python main.py --domain "多模态大模型安全检测" --top-k 5
python agent-system/backend/app_server.py
```

## 验证建议

```bash
python -m py_compile main.py agent-core/main.py agent-system/backend/app_server.py
python -m pytest
```
