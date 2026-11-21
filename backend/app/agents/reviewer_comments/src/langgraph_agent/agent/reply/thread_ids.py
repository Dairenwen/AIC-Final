"""SourceReplyGraph 的 thread_id 策略，供图节点与门面复用。"""

from __future__ import annotations

import hashlib
from uuid import UUID


def legacy_reply_thread_id(workspace_id: UUID, source_id: UUID) -> str:
    """返回升级前使用的来源级固定 thread_id。"""
    return f"workspace:{workspace_id}:reply:{source_id}"


def build_reply_thread_id(
    workspace_id: UUID,
    source_id: UUID,
    input_version: str,
) -> str:
    """为每个回复输入版本生成独立 checkpoint thread。"""
    version_digest = hashlib.sha256(input_version.encode("utf-8")).hexdigest()[:16]
    return (
        f"{legacy_reply_thread_id(workspace_id, source_id)}:"
        f"version:{version_digest}"
    )
