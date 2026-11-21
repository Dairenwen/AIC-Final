"""Agent 网关 —— 按 agent_id 把请求转发到对应的独立 Agent 服务（HTTP + SSE）。

设计要点：
- 每个 Agent 是一个独立 HTTP 服务，对外暴露 `GET /health` 和 `POST /invoke`（SSE）。
- 平台后端只做转发：把 Agent 返回的 SSE 事件原样透传给前端。
- 服务地址通过环境变量配置（见 backend/.env.example）；未配置的 Agent 会返回友好的「尚未接入」提示。
- 契约详见 docs/agent-integration.md。
"""

import os
import json
from typing import AsyncGenerator, Dict, Optional

from dotenv import load_dotenv

load_dotenv()


# agent_id -> 环境变量名。为空表示尚未接入。
_AGENT_ENV = {
    "drawing": "AGENT_DRAWING_URL",
    "innovation": "AGENT_INNOVATION_URL",
    "copyright": "AGENT_COPYRIGHT_URL",
    "patent": "AGENT_PATENT_URL",
    "translation": "AGENT_TRANSLATION_URL",
}

# agent_id -> 中文名（用于提示）
_AGENT_LABEL = {
    "drawing": "绘图创作",
    "innovation": "创新挖掘",
    "copyright": "软著文书",
    "patent": "专利文书",
    "translation": "学术翻译",
}

# 请求超时：连接 10s，读取不设上限（Agent 可能是长任务）
_CONNECT_TIMEOUT = 10.0


def _sse(event: str, data: dict) -> str:
    """构造一条 SSE 事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def get_agent_url(agent_id: str) -> Optional[str]:
    """读取某 Agent 的服务地址；未配置返回 None。"""
    env_name = _AGENT_ENV.get(agent_id)
    if not env_name:
        return None
    url = (os.getenv(env_name) or "").strip().rstrip("/")
    return url or None


def is_gateway_agent(agent_id: str) -> bool:
    """该 agent_id 是否走网关（即由独立服务实现，而非平台内置的 writing）。"""
    return agent_id in _AGENT_ENV


def agent_status() -> list:
    """返回各 Agent 的接入状态，供前端点亮可用智能体。"""
    result = [{
        "agent_id": "writing",
        "label": "Document Assistant",
        "integrated": True,
        "builtin": True,
    }]
    for agent_id in _AGENT_ENV:
        result.append({
            "agent_id": agent_id,
            "label": _AGENT_LABEL.get(agent_id, agent_id),
            "integrated": bool(get_agent_url(agent_id)),
            "builtin": False,
        })
    return result


async def proxy_agent(
    agent_id: str,
    payload: Dict,
    conversation_id: str,
) -> AsyncGenerator[str, None]:
    """把请求转发到对应 Agent 服务，并透传其 SSE 事件流。

    失败/未接入时，发送友好的 status + done，保证前端流程闭合。
    """
    label = _AGENT_LABEL.get(agent_id, agent_id)
    url = get_agent_url(agent_id)

    # 尚未配置服务地址
    if not url:
        yield _sse("status", {
            "step": "pending",
            "label": "未接入",
            "detail": f"「{label}」智能体尚未接入平台，敬请期待。",
        })
        yield _sse("token", {
            "content": f"「{label}」智能体正在开发中，暂未接入。请先使用「Document Assistant」。",
        })
        yield _sse("done", {"conversation_id": conversation_id})
        return

    try:
        import httpx
    except ImportError:
        yield _sse("error", {"message": "网关缺少 httpx 依赖，请在后端环境执行 pip install httpx"})
        yield _sse("done", {"conversation_id": conversation_id})
        return

    invoke_url = f"{url}/invoke"
    timeout = httpx.Timeout(_CONNECT_TIMEOUT, read=None, write=30.0, pool=None)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                invoke_url,
                json=payload,
                headers={"Accept": "text/event-stream"},
            ) as resp:
                if resp.status_code != 200:
                    yield _sse("error", {
                        "message": f"「{label}」服务返回异常状态码 {resp.status_code}",
                    })
                    yield _sse("done", {"conversation_id": conversation_id})
                    return

                # 原样透传上游 SSE 字节流（直接 yield bytes，避免在 chunk 边界
                # 切断多字节 UTF-8 字符导致中文乱码；Starlette 支持 bytes/str 混用）
                saw_done = False
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    if b"event: done" in chunk or b"event:done" in chunk:
                        saw_done = True
                    yield chunk

                # 上游若未发 done，补一个，保证前端闭合
                if not saw_done:
                    yield _sse("done", {"conversation_id": conversation_id})

    except httpx.ConnectError:
        yield _sse("error", {
            "message": f"无法连接「{label}」服务（{url}），请确认该服务已启动。",
        })
        yield _sse("done", {"conversation_id": conversation_id})
    except httpx.ReadTimeout:
        yield _sse("error", {"message": f"「{label}」服务响应超时。"})
        yield _sse("done", {"conversation_id": conversation_id})
    except Exception as e:
        yield _sse("error", {"message": f"转发到「{label}」服务出错：{str(e)}"})
        yield _sse("done", {"conversation_id": conversation_id})
