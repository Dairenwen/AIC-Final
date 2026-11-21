# Agent 接入规范（Integration Spec）

> 本文是**平台与各智能体（Agent）之间的接口契约**。做「绘图生成 / 创新挖掘 / 软著文书 / 专利文书 / 学术翻译」的同学，照本文实现一个 HTTP 服务即可接入，无需改动平台后端代码。

---

## 1. 总体架构

```
┌───────────┐      /api/chat/stream (SSE)      ┌──────────────────┐
│  前端 Web  │ ───────────────────────────────▶ │   平台后端(网关)   │
└───────────┘ ◀─────────────────────────────── │ agent-system/backend │
                    SSE 事件流                    └───────┬──────────┘
                                                         │ 按 agent_id 转发
                             POST /invoke (SSE)          │
                 ┌───────────────────────────────────────┼───────────────────────┐
                 ▼                     ▼                  ▼                        ▼
          ┌────────────┐      ┌──────────────┐    ┌──────────────┐        ┌──────────────┐
          │ 绘图 Agent  │      │ 创新挖掘 Agent │    │ 软著 Agent    │        │ 专利 Agent    │
          │ 独立服务    │      │ 独立服务      │    │ 独立服务      │        │ 独立服务      │
          └────────────┘      └──────────────┘    └──────────────┘        └──────────────┘
```

- 平台后端是**网关 + 编排层**：接收前端请求，按 `agent_id` 转发到对应 Agent 服务，并把 Agent 返回的 SSE 事件**原样透传**给前端。
- 每个 Agent 是**独立的 HTTP 服务**，自带自己的依赖栈（LangChain 版本、模型、工具随意），互不影响。
- 你只需要把服务地址告诉平台（配置一个环境变量），就能接入。

---

## 2. 为什么用 HTTP + SSE（而不是直接给平台一个 Python 包）

| 维度 | 直接 import Python 包 | HTTP 独立服务（本方案） |
|------|----------------------|------------------------|
| 依赖冲突 | 各 Agent 的 langchain 等版本挤在一个环境，极易冲突 | 各自独立环境，互不影响 |
| 独立开发 | 必须共用一套代码库和环境 | 各自开发、各自跑、各自调试 |
| 语言限制 | 必须 Python | 任意语言，只要能开 HTTP 服务 |
| 故障隔离 | 一个 Agent 崩溃拖垮整个后端 | 单个服务挂了不影响其它 |
| 部署 | 一起部署 | 可各自部署到不同机器 |

结论：**每个 Agent = 一个独立 HTTP 服务**，对外暴露两个接口：`GET /health` 和 `POST /invoke`。

---

## 3. 你需要实现的两个接口

### 3.1 `GET /health` — 健康检查

平台会用它判断你的服务是否在线。

**响应（200，JSON）：**
```json
{ "status": "ok", "agent_id": "drawing" }
```

### 3.2 `POST /invoke` — 核心调用接口（SSE 流式）

- **请求**：`Content-Type: application/json`，body 见第 4 节。
- **响应**：`Content-Type: text/event-stream`，用 SSE 逐步返回事件，事件类型见第 5 节。
- 处理完成后**必须**以一个 `done` 事件结束。

> SSE 格式提醒：每个事件是 `event: <类型>\ndata: <JSON字符串>\n\n`（两个换行结尾）。响应头建议带 `Cache-Control: no-cache`、`X-Accel-Buffering: no`，避免被缓冲。

---

## 4. 请求体（平台 → Agent）

平台调用你的 `/invoke` 时会发送如下 JSON：

```json
{
  "message": "用户在输入框里输入的文本",
  "conversation_id": "会话唯一ID（同一会话多轮相同）",
  "history": [
    { "role": "user", "content": "上一轮用户说的" },
    { "role": "assistant", "content": "上一轮你回复的" }
  ],
  "files": [
    {
      "id": "文件ID",
      "name": "paper.pdf",
      "content": "文件已被平台解析成的纯文本（可直接用）",
      "preview": "前200字预览",
      "size": 123456,
      "raw_url": "http://<平台地址>/api/files/文件ID/raw"
    }
  ],
  "params": {
    "language": "zh",
    "topic": "可选：主题",
    "keywords": ["可选", "关键词"]
  },
  "resume": null
}
```

字段说明：

| 字段 | 类型 | 说明 |
|------|------|------|
| `message` | string | 用户本轮输入 |
| `conversation_id` | string | 会话 ID。**有状态的 Agent（如软著）必须用它做 checkpoint**，以便门禁恢复 |
| `history` | array | 最近若干轮对话历史（可能为空） |
| `files` | array | 用户上传的文件。`content` 是**平台已解析好的纯文本**，直接用即可；需要原始文件（如软著要整份源码、专利要图）时用 `raw_url` 下载 |
| `params` | object | 通用参数 + Agent 专属参数。`language` 是输出语言（zh/en） |
| `resume` | object/null | **门禁恢复用**（仅有人工门禁的 Agent 需要），见第 6 节。首次调用为 `null` |

---

## 5. 事件协议（Agent → 平台 → 前端）

你在 `/invoke` 的 SSE 响应里可以发送以下 6 种事件。前端会据此渲染。

| 事件类型 | 用途 | 谁会用到 |
|----------|------|----------|
| `status` | 思考过程的一个步骤 | 所有 |
| `token` | 流式文本增量（逐字/逐句输出） | 文稿/翻译/专利/创新总结 |
| `artifact` | 产物：图片 / DOCX / TXT / JSON 等 | 绘图/软著/专利/创新 |
| `gate` | 人工门禁：需要用户确认后才能继续 | 软著 |
| `error` | 出错信息 | 所有 |
| `done` | 结束（必须发） | 所有 |

### 5.1 `status` — 思考步骤

```
event: status
data: {"step": "searching", "label": "文献检索", "detail": "正在检索 arXiv：diffusion model ..."}
```
| 字段 | 说明 |
|------|------|
| `step` | 步骤标识（英文小写，如 `analyzing`/`drawing`/`generating`） |
| `label` | 步骤中文名（展示给用户） |
| `detail` | 该步骤的**动态**详情（尽量具体，别每次都一样） |

> 建议：一个步骤开始时发一次（进行中），结束时再发一次带最终 `detail` 的（用 `step` 加 `_done` 后缀，如 `searching_done`）。这样前端能显示"进行中→完成"。

### 5.2 `token` — 流式文本

```
event: token
data: {"content": "这是一段增量文本"}
```
前端会把多个 `token` 的 `content` 拼接显示。适合需要"打字机效果"的文本输出。

### 5.3 `artifact` — 产物

```
event: artifact
data: {"artifact_type": "image", "name": "架构图.png", "url": "http://<你的服务>/artifacts/abc.png", "mime": "image/png", "preview": "一句话描述"}
```
| 字段 | 说明 |
|------|------|
| `artifact_type` | `image` / `docx` / `txt` / `pdf` / `json` / `markdown` |
| `name` | 文件名/标题 |
| `url` | 产物下载/预览地址（**由你的服务托管**，见 5.6） |
| `mime` | MIME 类型 |
| `preview` | 文本类产物可直接给内容预览；也可省略 |

- **绘图**：生成图片 → 发 `artifact(image)`。
- **软著/专利**：生成 DOCX/TXT → 发 `artifact(docx)`。
- **创新挖掘**：结构化创新点 → 发 `artifact(json)` 或 `artifact(markdown)`。

### 5.4 `gate` — 人工门禁（软著专用）

见第 6 节详细流程。

```
event: gate
data: {"gate_id": "business", "stage": "业务理解确认", "summary": "识别行业为...主要功能为...", "options": [{"value":"confirm","label":"确认无误"},{"value":"modify","label":"我要修改"}], "required_fields": []}
```

### 5.5 `error` — 错误

```
event: error
data: {"message": "调用绘图模型失败：额度不足"}
```

### 5.6 `done` — 结束（必须）

```
event: done
data: {"conversation_id": "会话ID"}
```
收到 `done` 前端才认为本轮结束。**无论成功失败，最后都要发 `done`。**

---

## 6. 人工门禁与恢复（软著 / 需要多步确认的 Agent）

软著流程有多个必须用户确认的节点（选项目、确认业务理解、确认代码选择……）。流程如下：

```
第1次 POST /invoke (resume=null)
   Agent 执行到门禁 → 发 gate 事件 → 发 done 结束本次流
        │
        ▼ 前端弹出确认卡片，用户点"确认/修改"
第2次 POST /invoke (conversation_id 不变, resume={...})
   Agent 用 conversation_id 恢复之前的状态(checkpoint) → 继续到下一个门禁或结束
```

`resume` 结构：
```json
{
  "gate_id": "business",
  "action": "confirm",
  "payload": { "note": "行业改成教育", "fields": { } }
}
```

要点：
- **有状态 Agent 必须以 `conversation_id` 为 key 做状态持久化**（LangGraph 的 checkpointer + thread_id 天然适配）。平台不保存你的中间状态。
- 一次 `/invoke` 里可以经过多个不需要用户干预的步骤，遇到需要用户确认的点再发 `gate` 并结束。
- 无门禁的 Agent（绘图/翻译/创新/专利）忽略 `resume` 即可。

---

## 7. 各 Agent 的事件使用建议

| Agent | 典型事件序列 |
|-------|-------------|
| **绘图生成** | `status(理解需求)` → `status(生成中)` → `artifact(image)` → `done` |
| **创新挖掘** | `status(文献检索)` → `status(趋势分析)` → `status(空白识别)` → `token(创新点说明)` → `artifact(json 创新点列表)` → `done` |
| **软著文书** | `status(项目分析)` → `gate(确认项目)` → `done`；恢复后 `status(业务理解)` → `gate(确认业务)` → ... → `artifact(docx)` → `done` |
| **专利文书** | `status(解析交底书)` → `status(检索现有专利)` → `token(权利要求书草稿)` → `artifact(docx)` → `done` |

---

## 8. 如何接入平台

1. 你的服务在某个地址跑起来，比如 `http://127.0.0.1:9001`。
2. 告诉平台维护者你的 `agent_id` 和地址，平台在 `agent-system/backend/.env` 里配置对应环境变量：
   ```
   AGENT_DRAWING_URL=http://127.0.0.1:9001
   AGENT_INNOVATION_URL=http://127.0.0.1:9002
   AGENT_COPYRIGHT_URL=http://127.0.0.1:9003
   AGENT_PATENT_URL=http://127.0.0.1:9004
   AGENT_TRANSLATION_URL=http://127.0.0.1:9005
   ```
3. 平台重启后，前端选择对应智能体，请求就会被转发到你的服务。未配置地址的智能体，前端会提示"尚未接入"。

`agent_id` 约定：`drawing` / `innovation` / `copyright` / `patent` / `translation`。

---

## 9. 本地联调与自测 checklist

自测（不依赖平台，直接 curl 你的服务）：
```bash
# 健康检查
curl http://127.0.0.1:9001/health

# 调用（观察是否按 SSE 逐步返回，且最后有 done）
curl -N -X POST http://127.0.0.1:9001/invoke \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"帮我画一张系统架构图\",\"conversation_id\":\"test-1\",\"history\":[],\"files\":[],\"params\":{\"language\":\"zh\"},\"resume\":null}"
```

接入前请确认：
- [ ] `GET /health` 返回 `{"status":"ok"}`
- [ ] `POST /invoke` 返回 `text/event-stream`，事件格式为 `event:\ndata:\n\n`
- [ ] 事件 `data` 是**合法 JSON**（用 `ensure_ascii=false` 输出中文）
- [ ] 无论成功失败，**最后都发 `done`**
- [ ] 产物有可访问的 `url`（跨机器时注意不要用 `localhost`）
- [ ] 有门禁的：`conversation_id` 能正确恢复状态
- [ ] 长任务不要一次性阻塞太久无输出，多发 `status` 让用户看到进度

---

## 10. 最小可运行示例

见 `agents/_template_agent/`：一个用 FastAPI 写好的模板服务，已实现 `/health` 和 `/invoke`，并演示了 `status / token / artifact / done` 的发送。**克隆它，把核心逻辑换成你自己的即可。**

```bash
cd agents/_template_agent
pip install -r requirements.txt
python agent_service.py   # 默认监听 9001
```
