"""引言 Agent —— 撰写论文引言部分"""

from .base_agent import BaseAgent
from ..orchestrator.state import PaperState
from ..prompts.introduction import INTRODUCTION_SYSTEM_PROMPT


class IntroductionAgent(BaseAgent):
    """引言写作Agent：漏斗式结构，从宽泛背景聚焦到具体问题"""

    section_name = "introduction"
    section_title = "Introduction"

    def _get_system_prompt(self) -> str:
        return INTRODUCTION_SYSTEM_PROMPT

    def _build_user_prompt(self, state: PaperState) -> str:
        context = self._get_context_from_state(state)

        contributions = state.get("contributions", [])
        contributions_text = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(contributions))

        prompt = f"""
请撰写论文的引言（Introduction）部分。

{context}

贡献点：
{contributions_text if contributions_text else "请根据主题自行推断"}

补充说明：{state.get('additional_context', '无')}

写作结构要求（漏斗式）：
1. 研究领域的宏观背景（1-2段）
2. 具体研究问题的引出（1段）
3. 现有方法的不足与挑战（1段）
4. 本文的解决方案概述（1段）
5. 主要贡献点列表（编号列出）
6. 论文结构概览（可选，1段）

语言: {state.get('language', 'en')}
"""
        return prompt
