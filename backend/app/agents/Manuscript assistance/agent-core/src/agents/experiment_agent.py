"""实验 Agent —— 实验设计与结果分析"""

from .base_agent import BaseAgent
from ..orchestrator.state import PaperState
from ..prompts.experiment import EXPERIMENT_SYSTEM_PROMPT


class ExperimentAgent(BaseAgent):
    """实验写作Agent：设置、结果、分析、消融"""

    section_name = "experiment"
    section_title = "Experiments"

    def _get_system_prompt(self) -> str:
        return EXPERIMENT_SYSTEM_PROMPT

    def _build_user_prompt(self, state: PaperState) -> str:
        context = self._get_context_from_state(state)

        prompt = f"""
请撰写论文的实验（Experiments）部分。

{context}

写作结构要求：
1. 实验设置（Experimental Setup）
   - 数据集描述（名称、规模、划分、统计信息）
   - 评价指标定义
   - 实现细节（超参数、硬件环境、训练细节）
   - 基线方法介绍

2. 主实验结果（Main Results）
   - 用表格呈现与基线的对比
   - 对结果进行分析和解释
   - 突出本方法的优势

3. 消融实验（Ablation Study）
   - 验证各个组件的贡献
   - 表格或图表展示

4. 分析与讨论（Analysis）
   - 案例分析（Case Study，可选）
   - 参数敏感性分析（可选）
   - 可视化分析（可选）

注意事项：
- 表格使用 Markdown 或 LaTeX 格式
- 数据描述要精确（具体数字）
- 分析要有深度，不能只描述现象
- 对不足之处也要诚实讨论

语言: {state.get('language', 'en')}
"""
        return prompt
