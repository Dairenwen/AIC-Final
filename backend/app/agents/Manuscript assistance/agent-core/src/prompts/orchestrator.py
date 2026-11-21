"""编排Agent提示词"""

ORCHESTRATOR_SYSTEM_PROMPT = """你是一个学术论文写作的编排助手。你的职责是：
1. 理解用户的论文写作需求
2. 将任务分解到合适的子模块
3. 协调各章节之间的一致性
4. 对产出进行质量评估

你需要确保：
- 各章节的术语和符号保持一致
- 论文整体逻辑通顺
- 引用和参考文献格式统一
- 贡献点在引言和总结中前后呼应
"""

OUTLINE_PROMPT = """请为以下论文生成一个详细的写作大纲：

论文主题：{topic}
关键词：{keywords}
贡献点：
{contributions}

请生成包含以下部分的大纲，每个部分列出2-4个子要点：
1. Abstract（摘要）
2. Introduction（引言）
3. Related Work（相关工作）
4. Method（方法）
5. Experiments（实验）
6. Conclusion（总结）

要求：
- 每个子要点用1-2句话描述要写的内容
- 标注各部分之间的逻辑衔接关系
- 相关工作部分列出需要综述的方向分组
"""

QUALITY_CHECK_PROMPT = """请评估以下论文章节的写作质量。

章节名称：{section_name}
章节内容：
{content}

评估维度（每项0-1分）：
1. 结构完整性：是否包含必要的子部分
2. 逻辑连贯性：段落之间是否有清晰的逻辑关系
3. 学术规范性：用词、表达是否符合学术写作规范
4. 信息充分性：是否提供了足够的细节和论证
5. 创新表达：是否恰当突出了本文的创新点

请返回JSON格式：
{{
    "scores": {{
        "structure": float,
        "coherence": float,
        "academic_style": float,
        "informativeness": float,
        "novelty_expression": float
    }},
    "overall_score": float,
    "feedback": "具体改进建议",
    "pass": bool
}}
"""
