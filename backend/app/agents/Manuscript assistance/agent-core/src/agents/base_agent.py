"""子Agent基类 —— 定义所有写作Agent的公共接口和行为"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from ..orchestrator.state import PaperState, SectionContent
from ..config import config


class BaseAgent(ABC):
    """论文章节写作Agent基类"""

    # 子类需要覆盖
    section_name: str = ""
    section_title: str = ""

    def __init__(self, llm: ChatOpenAI, tools: Optional[List] = None):
        self.llm = llm
        self.tools = tools or []

    @abstractmethod
    def _get_system_prompt(self) -> str:
        """返回当前Agent的系统提示词"""
        ...

    @abstractmethod
    def _build_user_prompt(self, state: PaperState) -> str:
        """根据当前状态构建用户提示词"""
        ...

    def _get_context_from_state(self, state: PaperState) -> str:
        """从全局状态中提取当前Agent需要的上下文"""
        context_parts = []

        # 论文主题和大纲
        if state.get("paper_topic"):
            context_parts.append(f"论文主题: {state['paper_topic']}")
        if state.get("paper_outline"):
            context_parts.append(f"论文大纲:\n{state['paper_outline']}")
        if state.get("keywords"):
            context_parts.append(f"关键词: {', '.join(state['keywords'])}")
        if state.get("contributions"):
            context_parts.append(f"贡献点:\n" + "\n".join(
                f"  - {c}" for c in state["contributions"]
            ))

        # 已完成的其他章节摘要（用于保持一致性）
        sections = state.get("sections", {})
        if sections:
            context_parts.append("已完成章节摘要:")
            for name, sec in sections.items():
                if name != self.section_name:
                    content = sec.get("content", "")[:300]
                    context_parts.append(f"  [{name}]: {content}...")

        return "\n\n".join(context_parts)

    async def run(self, state: PaperState) -> SectionContent:
        """执行章节写作"""
        system_prompt = self._get_system_prompt()
        user_prompt = self._build_user_prompt(state)

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        response = await self.llm.ainvoke(messages)
        content = response.content

        # 构建章节内容
        result: SectionContent = {
            "title": self.section_title,
            "content": content,
            "word_count": len(content.split()),
            "quality_score": 0.0,  # 后续由质量检查填充
            "iteration_count": 1,
        }

        return result

    async def revise(self, state: PaperState, feedback: str) -> SectionContent:
        """根据反馈修改章节"""
        current_content = state.get("sections", {}).get(self.section_name, {})

        revision_prompt = f"""
请根据以下反馈修改"{self.section_title}"章节：

当前内容：
{current_content.get('content', '')}

反馈意见：
{feedback}

请输出修改后的完整章节内容。
"""
        messages = [
            SystemMessage(content=self._get_system_prompt()),
            HumanMessage(content=revision_prompt),
        ]

        response = await self.llm.ainvoke(messages)

        iteration = current_content.get("iteration_count", 0) + 1

        return {
            "title": self.section_title,
            "content": response.content,
            "word_count": len(response.content.split()),
            "quality_score": 0.0,
            "iteration_count": iteration,
        }
