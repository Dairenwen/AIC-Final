"""语法检查与学术表达润色工具"""

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from ..config import config


@tool
def check_academic_style(text: str) -> str:
    """
    检查文本是否符合学术写作规范，并给出修改建议。

    Args:
        text: 待检查的文本段落

    Returns:
        检查结果和改进建议
    """
    llm = ChatOpenAI(
        model=config.llm.model,
        api_key=config.llm.api_key,
        base_url=config.llm.api_base,
        temperature=0.3,
    )

    prompt = f"""请检查以下学术文本的写作质量，指出问题并给出修改建议：

文本：
{text}

请从以下维度检查：
1. 语法错误
2. 非学术表达（口语化、非正式用词）
3. 冗余表达
4. 逻辑不清晰的句子
5. 主被动语态使用是否恰当

输出格式：
- 问题列表（标注位置和类型）
- 修改建议
- 修改后的文本（如有必要）
"""
    response = llm.invoke(prompt)
    return response.content


@tool
def polish_text(text: str, style: str = "academic") -> str:
    """
    对文本进行学术润色。

    Args:
        text: 待润色文本
        style: 写作风格 (academic / concise / formal)

    Returns:
        润色后的文本
    """
    llm = ChatOpenAI(
        model=config.llm.model,
        api_key=config.llm.api_key,
        base_url=config.llm.api_base,
        temperature=0.4,
    )

    style_guide = {
        "academic": "正式学术风格，使用被动语态，专业术语准确",
        "concise": "简洁精炼，删除冗余，每句承载有效信息",
        "formal": "正式语体，适合顶会/顶刊投稿",
    }

    prompt = f"""请对以下文本进行学术润色：

风格要求：{style_guide.get(style, style_guide['academic'])}

原文：
{text}

要求：
- 保持原意不变
- 提升表达的学术性和专业性
- 优化句式结构
- 确保逻辑连贯

请直接输出润色后的文本。
"""
    response = llm.invoke(prompt)
    return response.content


GrammarCheckTool = [check_academic_style, polish_text]
