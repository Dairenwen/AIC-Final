"""通用辅助函数"""

import re
import json
from typing import Optional, Any


def count_words(text: str, language: str = "en") -> int:
    """统计文本字数

    Args:
        text: 输入文本
        language: 语言（en=按空格分词, zh=按字符计数）

    Returns:
        字/词数量
    """
    if language == "zh":
        # 中文按字符统计（去除空格和标点）
        chinese_chars = re.findall(r"[\u4e00-\u9fff]", text)
        return len(chinese_chars)
    else:
        # 英文按空格分词
        words = text.split()
        return len(words)


def truncate_text(text: str, max_length: int = 500, suffix: str = "...") -> str:
    """截断文本到指定长度

    Args:
        text: 原始文本
        max_length: 最大字符数
        suffix: 截断后缀

    Returns:
        截断后的文本
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


def extract_json_from_text(text: str) -> Optional[Any]:
    """从LLM输出文本中提取JSON对象

    支持从 markdown 代码块或原始文本中提取。

    Args:
        text: 包含JSON的文本

    Returns:
        解析后的Python对象，失败返回None
    """
    # 尝试从 ```json ... ``` 代码块提取
    json_match = re.search(r"```json\s*([\s\S]*?)\s*```", text)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试从 ``` ... ``` 代码块提取
    code_match = re.search(r"```\s*([\s\S]*?)\s*```", text)
    if code_match:
        try:
            return json.loads(code_match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试直接解析整段文本
    # 寻找第一个 { 和最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    return None


def format_section_output(section_name: str, content: str, format_type: str = "markdown") -> str:
    """格式化章节输出

    Args:
        section_name: 章节名称
        content: 章节内容
        format_type: 格式类型 (markdown / latex)

    Returns:
        格式化后的文本
    """
    if format_type == "latex":
        section_commands = {
            "abstract": "\\begin{abstract}\n{content}\n\\end{abstract}",
            "introduction": "\\section{{Introduction}}\n{content}",
            "related_work": "\\section{{Related Work}}\n{content}",
            "method": "\\section{{Method}}\n{content}",
            "experiment": "\\section{{Experiments}}\n{content}",
            "conclusion": "\\section{{Conclusion}}\n{content}",
        }
        template = section_commands.get(section_name, "\\section{{{name}}}\n{content}")
        return template.format(content=content, name=section_name)
    else:
        # Markdown 格式
        titles = {
            "abstract": "Abstract",
            "introduction": "1. Introduction",
            "related_work": "2. Related Work",
            "method": "3. Method",
            "experiment": "4. Experiments",
            "conclusion": "5. Conclusion",
        }
        title = titles.get(section_name, section_name.replace("_", " ").title())
        return f"## {title}\n\n{content}"
