"""论文写作工作流状态定义"""

from typing import TypedDict, Dict, List, Optional, Annotated
from operator import add


class SectionContent(TypedDict):
    """单个章节内容"""
    title: str
    content: str
    word_count: int
    quality_score: float
    iteration_count: int


class PaperState(TypedDict):
    """论文写作全局状态 —— LangGraph StateGraph 的核心状态"""

    # 用户输入
    user_input: str                         # 用户原始输入
    paper_topic: str                        # 论文主题
    keywords: List[str]                     # 关键词
    contributions: List[str]                # 贡献点
    target_section: Optional[str]           # 目标章节(None=全文)
    language: str                           # 目标语言
    additional_context: str                 # 补充说明

    # 过程数据
    paper_outline: str                      # 论文大纲
    references: List[dict]                  # 参考文献列表
    symbol_table: Dict[str, str]            # 全局符号表（保证一致性）

    # 各章节输出
    sections: Dict[str, SectionContent]     # 章节名 → 内容

    # 控制信息
    current_step: str                       # 当前执行步骤
    feedback: Annotated[List[str], add]     # 累积反馈（使用 reducer 追加）
    error: Optional[str]                    # 错误信息
    is_complete: bool                       # 是否完成
