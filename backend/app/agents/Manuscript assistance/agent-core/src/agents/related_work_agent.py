"""相关工作 Agent —— 文献综述与对比分析"""

from .base_agent import BaseAgent
from ..orchestrator.state import PaperState
from ..prompts.related_work import RELATED_WORK_SYSTEM_PROMPT


class RelatedWorkAgent(BaseAgent):
    """相关工作写作Agent：分类综述 + 指出research gap"""

    section_name = "related_work"
    section_title = "Related Work"

    def _get_system_prompt(self) -> str:
        return RELATED_WORK_SYSTEM_PROMPT

    def _build_user_prompt(self, state: PaperState) -> str:
        context = self._get_context_from_state(state)

        # 提取已有的参考文献信息
        references = state.get("references", [])
        ref_text = ""
        if references:
            ref_text = "\n已有参考文献：\n"
            for ref in references[:20]:  # 限制数量
                ref_text += f"  - {ref.get('title', 'Unknown')}: {ref.get('summary', '')[:100]}\n"

        prompt = f"""
请撰写论文的相关工作（Related Work）部分。

{context}
{ref_text}

写作结构要求：
1. 开头段落：概述相关工作的分类维度
2. 按主题分组综述（每组2-3段）：
   - 每组开头说明该方向的核心思想
   - 逐一介绍代表性工作及其特点
   - 每组结尾点出该方向的局限性
3. 总结段落：指出现有工作的整体gap，引出本文的不同

注意事项：
- 使用客观、中立的学术语言
- 对每项工作给出简洁但准确的描述
- 引用格式使用 [Author et al., Year] 占位
- 突出与本文方法的区别和联系

语言: {state.get('language', 'en')}
"""
        return prompt
