"""后端主入口 —— FastAPI 应用"""

import uuid
import os
from typing import Optional
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel

from .services.chat_service import ChatService
from .services.file_service import FileService
from .services import agent_gateway

app = FastAPI(title="Document Assistant API", version="0.2.0")

# 跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 服务实例
chat_service = ChatService()
file_service = FileService()


# ===== 数据模型 =====

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    agent_id: str = "writing"
    file_ids: Optional[list] = None  # 上传文件的ID列表
    topic: Optional[str] = None
    keywords: Optional[list] = None
    target_section: Optional[str] = None
    language: str = "zh"
    resume: Optional[dict] = None  # 人工门禁恢复对象（软著等有状态 Agent 使用）


# 后端对外可达地址（供独立 Agent 服务回取原始文件）
BACKEND_PUBLIC_URL = os.getenv("BACKEND_PUBLIC_URL", "http://localhost:8001")


# ===== 路由 =====

@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    """流式对话接口 —— 使用 SSE 逐步返回内容。

    按 agent_id 分发：
    - "writing"：使用后端内置的 Document Assistant 逻辑（chat_service）
    - 其它：作为网关，转发到已注册的独立 Agent 服务（agent_gateway）
    """
    conv_id = request.conversation_id or str(uuid.uuid4())

    # 获取上传文件的已解析内容及元信息
    file_records = []
    if request.file_ids:
        for file_id in request.file_ids:
            file_info = file_service.get_file_info(file_id)
            if file_info:
                # 附上原始文件下载地址，供需要原文件的 Agent（软著/专利）回取
                file_info = {
                    **file_info,
                    "raw_url": f"{BACKEND_PUBLIC_URL}/api/files/{file_info['id']}/raw",
                }
                file_records.append(file_info)

    if request.agent_id in (None, "", "writing"):
        # 内置 Document Assistant
        generator = chat_service.stream_response(
            message=request.message,
            conversation_id=conv_id,
            agent_id=request.agent_id or "writing",
            file_records=file_records,
            topic=request.topic,
            keywords=request.keywords,
            target_section=request.target_section,
            language=request.language,
        )
    else:
        # 网关转发到独立 Agent 服务
        payload = {
            "message": request.message,
            "conversation_id": conv_id,
            "files": file_records,
            "params": {
                "language": request.language,
                "topic": request.topic,
                "keywords": request.keywords,
                "target_section": request.target_section,
            },
            "resume": request.resume,
        }
        generator = agent_gateway.proxy_agent(request.agent_id, payload, conv_id)

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
            "X-Conversation-Id": conv_id,
            "Transfer-Encoding": "chunked",
        },
    )


@app.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    """批量上传并解析文件（支持 PDF、TXT、MD、DOCX、TEX）。"""
    if not files:
        raise HTTPException(status_code=400, detail="请选择至少一个文件")

    try:
        results = [await file_service.save_and_parse(file) for file in files]
        return {"files": results}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件处理失败: {str(e)}")


@app.get("/api/files/{file_id}/raw")
async def get_raw_file(file_id: str):
    """返回上传文件的原始内容（供软著/专利等需要原文件的 Agent 回取）。"""
    path = file_service.get_raw_path(file_id)
    if not path:
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path)


@app.get("/api/agents")
async def list_agents():
    """返回各 Agent 的接入状态（前端可据此点亮可用智能体）。"""
    return {"agents": agent_gateway.agent_status()}


@app.get("/api/conversations")
async def list_conversations():
    """获取历史会话"""
    return {"conversations": chat_service.get_conversations()}


@app.get("/api/conversations/{conversation_id}/messages")
async def get_conversation_messages(conversation_id: str):
    """获取指定会话的消息记录"""
    messages = chat_service.get_messages(conversation_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"messages": messages}


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """删除会话"""
    chat_service.delete_conversation(conversation_id)
    return {"status": "ok"}
