"""摘要 Agent —— 生成结构化学术摘要"""

from .base_agent import BaseAgent
from ..orchestrator.state import PaperState
from ..prompts.abstract import ABSTRACT_SYSTEM_PROMPT


class AbstractAgent(BaseAgent):
    """摘要写作Agent：基于全文内容生成结构化摘要"""

    section_name = "abstract"
    section_title = "Abstract"

    def _get_system_prompt(self) -> str:
        return ABSTRACT_SYSTEM_PROMPT

    def _build_user_prompt(self, state: PaperState) -> str:
        context = self._get_context_from_state(state)

        # 摘要生成需要全文各章节的核心信息
        sections = state.get("sections", {})
        section_summaries = []
        for name in ["introduction", "related_work", "method", "experiment", "conclusion"]:
            sec = sections.get(name, {})
            if sec.get("content"):
                section_summaries.append(f"[{name}]:\n{sec['content'][:500]}")

        prompt = f"""
请为以下论文生成学术摘要。

{context}

各章节核心内容：
{"".join(section_summaries) if section_summaries else "暂无（请基于主题和贡献点生成）"}

要求：
- 语言: {state.get('language', 'en')}
- 结构: 背景 → 问题 → 方法 → 结果 → 结论
- 字数: 150-300词（英文）/ 300-500字（中文）
- 包含关键词
"""
        return prompt
