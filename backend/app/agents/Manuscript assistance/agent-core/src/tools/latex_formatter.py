"""LaTeX 格式化工具"""

import re
from langchain_core.tools import tool


@tool
def format_equation(equation: str, label: str = "") -> str:
    """
    将数学表达式格式化为 LaTeX 公式格式。

    Args:
        equation: 数学表达式
        label: 公式标签（用于引用）

    Returns:
        格式化后的 LaTeX 公式
    """
    if label:
        return f"\\begin{{equation}}\n\\label{{eq:{label}}}\n{equation}\n\\end{{equation}}"
    return f"$$\n{equation}\n$$"


@tool
def format_algorithm(
    title: str,
    inputs: str,
    outputs: str,
    steps: list,
) -> str:
    """
    生成 LaTeX 算法伪代码格式。

    Args:
        title: 算法标题
        inputs: 输入描述
        outputs: 输出描述
        steps: 算法步骤列表

    Returns:
        LaTeX 格式的算法描述
    """
    lines = [
        "\\begin{algorithm}[H]",
        f"\\caption{{{title}}}",
        "\\begin{algorithmic}[1]",
        f"\\REQUIRE {inputs}",
        f"\\ENSURE {outputs}",
    ]

    for step in steps:
        if step.startswith("FOR"):
            lines.append(f"\\FOR{{{step[4:]}}}")
        elif step.startswith("ENDFOR"):
            lines.append("\\ENDFOR")
        elif step.startswith("IF"):
            lines.append(f"\\IF{{{step[3:]}}}")
        elif step.startswith("ENDIF"):
            lines.append("\\ENDIF")
        elif step.startswith("RETURN"):
            lines.append(f"\\RETURN {step[7:]}")
        else:
            lines.append(f"\\STATE {step}")

    lines.extend([
        "\\end{algorithmic}",
        "\\end{algorithm}",
    ])

    return "\n".join(lines)


@tool
def format_table(
    caption: str,
    headers: list,
    rows: list,
    label: str = "",
    bold_best: bool = True,
) -> str:
    """
    生成 LaTeX 表格。

    Args:
        caption: 表格标题
        headers: 表头列表
        rows: 数据行（二维列表）
        label: 表格标签
        bold_best: 是否加粗最优结果

    Returns:
        LaTeX 格式的表格
    """
    col_format = "l" + "c" * (len(headers) - 1)

    lines = [
        "\\begin{table}[t]",
        f"\\caption{{{caption}}}",
    ]
    if label:
        lines.append(f"\\label{{tab:{label}}}")

    lines.extend([
        "\\centering",
        f"\\begin{{tabular}}{{{col_format}}}",
        "\\toprule",
        " & ".join(headers) + " \\\\",
        "\\midrule",
    ])

    for row in rows:
        lines.append(" & ".join(str(cell) for cell in row) + " \\\\")

    lines.extend([
        "\\bottomrule",
        "\\end{tabular}",
        "\\end{table}",
    ])

    return "\n".join(lines)


@tool
def validate_latex(text: str) -> str:
    """
    验证文本中 LaTeX 公式的语法正确性。

    Args:
        text: 包含 LaTeX 公式的文本

    Returns:
        验证结果与错误提示
    """
    issues = []

    # 检查行内公式配对
    inline_count = text.count("$") - 2 * text.count("$$")
    if inline_count % 2 != 0:
        issues.append("行内公式 $ 未配对")

    # 检查 begin/end 配对
    begins = re.findall(r"\\begin\{(\w+)\}", text)
    ends = re.findall(r"\\end\{(\w+)\}", text)
    if begins != ends:
        issues.append(f"环境未配对: begin={begins}, end={ends}")

    # 检查常见错误
    if "\\frac" in text and text.count("{") != text.count("}"):
        issues.append("花括号未配对")

    if not issues:
        return "LaTeX 语法检查通过，未发现问题。"

    return "发现以下问题：\n" + "\n".join(f"- {issue}" for issue in issues)


LaTeXFormatterTool = [format_equation, format_algorithm, format_table, validate_latex]
