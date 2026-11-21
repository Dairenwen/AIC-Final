"""总结展望 Agent —— 工作总结与未来方向"""

from .base_agent import BaseAgent
from ..orchestrator.state import PaperState
from ..prompts.conclusion import CONCLUSION_SYSTEM_PROMPT


class ConclusionAgent(BaseAgent):
    """总结写作Agent：总结贡献 + 展望未来"""

    section_name = "conclusion"
    section_title = "Conclusion"

    def _get_system_prompt(self) -> str:
        return CONCLUSION_SYSTEM_PROMPT

    def _build_user_prompt(self, state: PaperState) -> str:
        context = self._get_context_from_state(state)

        # 从已完成章节中提取关键信息
        sections = state.get("sections", {})
        key_findings = []
        if "experiment" in sections:
            key_findings.append(f"实验结果: {sections['experiment'].get('content', '')[:300]}")

        prompt = f"""
请撰写论文的总结与展望（Conclusion / Conclusion and Future Work）部分。

{context}

关键发现：
{chr(10).join(key_findings) if key_findings else "请基于贡献点总结"}

写作结构要求：
1. 工作总结（1-2段）
   - 简要回顾研究问题
   - 概述提出的方法
   - 总结主要发现和贡献

2. 局限性（1段，可选但推荐）
   - 诚实指出当前方法的不足
   - 适用范围的限制

3. 未来工作（1段）
   - 2-4个具体的未来研究方向
   - 每个方向简要说明其价值

注意事项：
- 不要引入新的内容或方法细节
- 与引言中的贡献点呼应
- 语言精炼，避免重复全文内容
- 展望要具体可行，不能太空泛

语言: {state.get('language', 'en')}
"""
        return prompt
