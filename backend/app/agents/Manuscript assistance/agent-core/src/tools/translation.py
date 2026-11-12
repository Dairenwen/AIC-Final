"""学术翻译工具"""

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from ..config import config


@tool
def translate_academic(
    text: str,
    source_lang: str = "zh",
    target_lang: str = "en",
) -> str:
    """
    学术文本翻译，保持学术风格和专业术语准确性。

    Args:
        text: 待翻译文本
        source_lang: 源语言 (zh / en)
        target_lang: 目标语言 (en / zh)

    Returns:
        翻译后的文本
    """
    llm = ChatOpenAI(
        model=config.llm.model,
        api_key=config.llm.api_key,
        base_url=config.llm.api_base,
        temperature=0.3,
    )

    lang_map = {"zh": "中文", "en": "英文"}
    source = lang_map.get(source_lang, source_lang)
    target = lang_map.get(target_lang, target_lang)

    prompt = f"""请将以下{source}学术文本翻译为{target}。

要求：
1. 保持学术写作风格
2. 专业术语翻译准确（保留通用英文缩写如CNN, BERT等）
3. 句式符合{target}学术论文的表达习惯
4. 保留原文的LaTeX公式不翻译
5. 保持原文的段落结构

原文：
{text}

请直接输出翻译结果：
"""
    response = llm.invoke(prompt)
    return response.content


TranslationTool = [translate_academic]
