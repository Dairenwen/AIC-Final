"""
短期记忆 — LangChain ConversationBuffer + ConversationSummary 封装

- 内存缓冲区：最近 N 轮对话
- 滑动窗口摘要：超窗口自动压缩为摘要
- 会话隔离：session_id 区分不同会话
"""

from typing import Optional
from collections import defaultdict
from datetime import datetime
from utils.logger import get_logger

logger = get_logger(__name__)


class ShortTermMemory:
    """
    短期记忆管理器

    策略：
    - 保留最近 max_turns 轮完整对话 (buffer)
    - 超出部分自动生成摘要 (summary)
    - 按 session_id 隔离
    """

    def __init__(self, max_turns: int = 10, max_tokens_est: int = 8000):
        self.max_turns = max_turns
        self.max_tokens_est = max_tokens_est
        self._buffers: dict[str, list[dict]] = defaultdict(list)
        self._summaries: dict[str, str] = {}
        self._metadata: dict[str, dict] = {}

    def add_turn(self, session_id: str, role: str, content: str,
                 metadata: Optional[dict] = None):
        """添加一轮对话"""
        turn = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        self._buffers[session_id].append(turn)

        # 超出窗口时压缩旧对话为摘要
        if len(self._buffers[session_id]) > self.max_turns:
            self._compress(session_id)

        logger.debug(f"[{session_id}] 短期记忆: {len(self._buffers[session_id])} 轮")

    def get_context(self, session_id: str, last_n: Optional[int] = None) -> list[dict]:
        """获取会话上下文（最近 N 轮）"""
        buffer = self._buffers.get(session_id, [])
        if last_n:
            buffer = buffer[-last_n:]
        return buffer

    def get_summary(self, session_id: str) -> str:
        """获取会话摘要"""
        return self._summaries.get(session_id, "")

    def get_full_context(self, session_id: str) -> str:
        """获取完整上下文文本（摘要 + 最近对话）"""
        parts = []
        summary = self._summaries.get(session_id, "")
        if summary:
            parts.append(f"[历史摘要]\n{summary}")

        buffer = self._buffers.get(session_id, [])
        if buffer:
            recent = buffer[-self.max_turns:]
            parts.append("[最近对话]")
            for t in recent:
                parts.append(f"{t['role']}: {t['content'][:500]}")

        return "\n\n".join(parts)

    def _compress(self, session_id: str):
        """将最旧的对话压缩为摘要"""
        buffer = self._buffers[session_id]
        overflow = buffer[:-self.max_turns]

        # 合并旧内容
        old_text = "\n".join(
            f"{t['role']}: {t['content'][:300]}" for t in overflow
        )

        # 简单规则摘要（生产环境可调用 LLM 生成更好的摘要）
        existing = self._summaries.get(session_id, "")
        new_summary = f"已处理 {len(overflow)} 轮对话。要点: {old_text[:500]}..."

        self._summaries[session_id] = (existing + "\n" + new_summary).strip()
        self._buffers[session_id] = buffer[-self.max_turns:]

    def clear(self, session_id: str):
        """清除会话"""
        self._buffers.pop(session_id, None)
        self._summaries.pop(session_id, None)
        self._metadata.pop(session_id, None)

    def set_metadata(self, session_id: str, key: str, value):
        """设置会话元数据"""
        if session_id not in self._metadata:
            self._metadata[session_id] = {}
        self._metadata[session_id][key] = value

    def get_metadata(self, session_id: str) -> dict:
        """获取会话元数据"""
        return self._metadata.get(session_id, {})


# 全局单例
_short_term: Optional[ShortTermMemory] = None


def get_short_term_memory(max_turns: int = 10) -> ShortTermMemory:
    global _short_term
    if _short_term is None:
        _short_term = ShortTermMemory(max_turns=max_turns)
    return _short_term
