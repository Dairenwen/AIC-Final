"""一致性检查工具 —— 确保全文术语、符号、逻辑一致"""

from typing import Dict
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from ..config import config


@tool
def check_consistency(sections: Dict[str, str]) -> str:
    """
    检查论文各章节之间的一致性。

    Args:
        sections: 章节字典 {章节名: 内容}

    Returns:
        一致性检查报告
    """
    llm = ChatOpenAI(
        model=config.llm.model,
        api_key=config.llm.api_key,
        base_url=config.llm.api_base,
        temperature=0.2,
    )

    sections_text = "\n\n".join(
        f"=== {name} ===\n{content[:800]}"
        for name, content in sections.items()
    )

    prompt = f"""请检查以下论文各章节之间的一致性问题：

{sections_text}

请检查：
1. 术语一致性：同一概念是否使用相同的术语
2. 符号一致性：数学符号是否前后统一
3. 逻辑一致性：引言承诺的贡献是否在后续章节体现
4. 引用一致性：相关工作提到的方法是否在实验中对比
5. 数据一致性：不同地方提到的数字是否一致

输出格式：
- 问题类别
- 具体位置
- 问题描述
- 修改建议
"""
    response = llm.invoke(prompt)
    return response.content


@tool
def check_contribution_alignment(
    introduction: str,
    conclusion: str,
    contributions: list,
) -> str:
    """
    检查贡献点在引言和总结中是否前后呼应。

    Args:
        introduction: 引言内容
        conclusion: 总结内容
        contributions: 贡献点列表

    Returns:
        对齐检查结果
    """
    llm = ChatOpenAI(
        model=config.llm.model,
        api_key=config.llm.api_key,
        base_url=config.llm.api_base,
        temperature=0.2,
    )

    prompt = f"""请检查以下论文的贡献点是否在引言和总结中正确呼应：

贡献点列表：
{chr(10).join(f'{i+1}. {c}' for i, c in enumerate(contributions))}

引言（摘录）：
{introduction[:1000]}

总结（摘录）：
{conclusion[:1000]}

请逐一检查每个贡献点：
1. 是否在引言中明确提出
2. 是否在总结中有对应回顾
3. 表述是否一致

输出每个贡献点的对齐状态和改进建议。
"""
    response = llm.invoke(prompt)
    return response.content


ConsistencyCheckTool = [check_consistency, check_contribution_alignment]
