"""意图路由器 —— 解析用户意图并分发到对应Agent"""

from typing import Dict, Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from ..config import config
from ..utils.helpers import extract_json_from_text


# 支持的意图类型
INTENT_TYPES = {
    "full_paper": "生成完整论文",
    "single_section": "生成单个章节",
    "revise": "修改润色已有内容",
    "outline": "生成论文大纲",
    "translate": "翻译",
    "literature": "文献检索",
}

# 章节名称映射（支持中英文输入）
SECTION_NAME_MAP = {
    "摘要": "abstract",
    "abstract": "abstract",
    "引言": "introduction",
    "introduction": "introduction",
    "intro": "introduction",
    "相关工作": "related_work",
    "related work": "related_work",
    "related_work": "related_work",
    "方法": "method",
    "method": "method",
    "methodology": "method",
    "实验": "experiment",
    "experiment": "experiment",
    "experiments": "experiment",
    "总结": "conclusion",
    "结论": "conclusion",
    "conclusion": "conclusion",
    "展望": "conclusion",
}


class IntentRouter:
    """意图路由器"""

    def __init__(self, llm: Optional[ChatOpenAI] = None):
        self.llm = llm or ChatOpenAI(
            model=config.llm.model,
            api_key=config.llm.api_key,
            base_url=config.llm.api_base,
            temperature=0.1,
        )

    async def parse_intent(self, user_input: str) -> Dict:
        """解析用户输入的意图

        Args:
            user_input: 用户原始输入

        Returns:
            解析结果字典，包含:
            - intent: 意图类型
            - target_section: 目标章节（如适用）
            - topic: 论文主题
            - keywords: 关键词
            - contributions: 贡献点
            - additional_context: 补充信息
        """
        prompt = f"""请分析用户的论文写作请求，提取关键信息。

用户输入：
{user_input}

请返回JSON格式（不要markdown代码块之外的内容）：
```json
{{
    "intent": "full_paper | single_section | revise | outline | translate | literature",
    "target_section": "abstract | introduction | related_work | method | experiment | conclusion | null",
    "topic": "论文主题",
    "keywords": ["关键词1", "关键词2"],
    "contributions": ["贡献点1", "贡献点2"],
    "language": "en | zh",
    "additional_context": "其他补充信息"
}}
```

判断规则：
- 如果用户要求写整篇论文 → intent = "full_paper"
- 如果用户要求写某个具体章节 → intent = "single_section"
- 如果用户提供了已有内容要求修改 → intent = "revise"
- 如果用户只要大纲 → intent = "outline"
- 如果用户要翻译 → intent = "translate"
- 如果用户要查文献 → intent = "literature"
"""
        messages = [
            SystemMessage(content="你是一个精确的意图解析器，只输出JSON。"),
            HumanMessage(content=prompt),
        ]

        response = await self.llm.ainvoke(messages)
        result = extract_json_from_text(response.content)

        if result is None:
            # 解析失败，返回默认值
            return {
                "intent": "full_paper",
                "target_section": None,
                "topic": user_input,
                "keywords": [],
                "contributions": [],
                "language": "en",
                "additional_context": "",
            }

        # 标准化章节名称
        target = result.get("target_section")
        if target and target != "null":
            result["target_section"] = SECTION_NAME_MAP.get(
                target.lower(), target
            )
        else:
            result["target_section"] = None

        return result

    def quick_route(self, user_input: str) -> Optional[str]:
        """快速路由（基于关键词，不调用LLM）

        适用于简单、明确的指令。

        Args:
            user_input: 用户输入

        Returns:
            章节名称 或 None（需要LLM解析）
        """
        input_lower = user_input.lower().strip()

        # 直接匹配章节关键词
        for keyword, section in SECTION_NAME_MAP.items():
            if keyword in input_lower and ("写" in input_lower or "生成" in input_lower or "write" in input_lower):
                return section

        return None
