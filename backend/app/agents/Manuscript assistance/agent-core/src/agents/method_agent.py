"""方法 Agent —— 技术方案描述"""

from .base_agent import BaseAgent
from ..orchestrator.state import PaperState
from ..prompts.method import METHOD_SYSTEM_PROMPT


class MethodAgent(BaseAgent):
    """方法写作Agent：清晰描述技术方案的各个模块"""

    section_name = "method"
    section_title = "Method"

    def _get_system_prompt(self) -> str:
        return METHOD_SYSTEM_PROMPT

    def _build_user_prompt(self, state: PaperState) -> str:
        context = self._get_context_from_state(state)

        # 符号表用于保持公式一致性
        symbol_table = state.get("symbol_table", {})
        symbol_text = ""
        if symbol_table:
            symbol_text = "\n符号约定：\n"
            for sym, desc in symbol_table.items():
                symbol_text += f"  - {sym}: {desc}\n"

        prompt = f"""
请撰写论文的方法（Method/Methodology）部分。

{context}
{symbol_text}

写作结构要求：
1. 问题定义与形式化（Problem Formulation）
   - 用数学语言定义输入、输出、目标
2. 方法总览（Overview）
   - 用1-2段概述整体框架
   - 描述各模块之间的关系
3. 各子模块详述（按逻辑顺序）
   - 每个模块：动机 → 具体做法 → 公式/算法
4. 训练/优化目标（如适用）
   - 损失函数定义
   - 训练策略

注意事项：
- 公式使用 LaTeX 格式（$..$ 行内，$$...$$ 独立行）
- 保持符号的一致性
- 算法可用伪代码描述
- 突出创新点

语言: {state.get('language', 'en')}
"""
        return prompt
