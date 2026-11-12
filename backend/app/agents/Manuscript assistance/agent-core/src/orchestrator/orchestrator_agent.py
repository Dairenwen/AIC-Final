"""编排控制 Agent —— 负责任务分解、调度和质量控制。"""

import asyncio
from typing import Dict, Any
from langchain_openai import ChatOpenAI

from ..config import config
from .state import PaperState
from ..agents import (
    AbstractAgent,
    IntroductionAgent,
    RelatedWorkAgent,
    MethodAgent,
    ExperimentAgent,
    ConclusionAgent,
)
from ..prompts.orchestrator import ORCHESTRATOR_SYSTEM_PROMPT, OUTLINE_PROMPT


class OrchestratorAgent:
    """主编排 Agent，基于 LangGraph 构建有状态工作流"""

    # 章节执行顺序（摘要最后生成，因为需要全文信息）
    SECTION_ORDER = [
        "introduction",
        "related_work",
        "method",
        "experiment",
        "conclusion",
        "abstract",
    ]

    def __init__(self):
        self.llm = ChatOpenAI(
            model=config.llm.model,
            api_key=config.llm.api_key,
            base_url=config.llm.api_base,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )

        # 初始化各子Agent
        self.agents: Dict[str, Any] = {
            "abstract": AbstractAgent(self.llm),
            "introduction": IntroductionAgent(self.llm),
            "related_work": RelatedWorkAgent(self.llm),
            "method": MethodAgent(self.llm),
            "experiment": ExperimentAgent(self.llm),
            "conclusion": ConclusionAgent(self.llm),
        }

    # ==================== 节点实现 ====================

    async def _parse_input(self, state: PaperState) -> Dict:
        """解析用户输入，提取关键信息"""
        user_input = state["user_input"]

        parse_prompt = f"""
请从以下用户输入中提取论文写作的关键信息：
{user_input}

请提取：
1. 论文主题
2. 关键词（列表）
3. 贡献点（列表）
4. 目标章节（如果用户只想写某一章节）
5. 补充说明

以JSON格式返回。
"""
        response = await self.llm.ainvoke(parse_prompt)

        # 实际实现中应解析LLM返回的JSON
        return {
            "paper_topic": state.get("paper_topic", user_input),
            "current_step": "parse_input",
        }

    async def _generate_outline(self, state: PaperState) -> Dict:
        """生成论文大纲"""
        prompt = OUTLINE_PROMPT.format(
            topic=state["paper_topic"],
            keywords=", ".join(state.get("keywords", [])),
            contributions="\n".join(
                f"- {c}" for c in state.get("contributions", [])
            ),
        )

        response = await self.llm.ainvoke(prompt)

        return {
            "paper_outline": response.content,
            "current_step": "generate_outline",
        }

    async def _write_independent_sections(self, state: PaperState) -> None:
        """Generate mutually independent body sections concurrently.

        Each coroutine receives an immutable snapshot so prompt construction never
        races with another section's output.  The result merge follows the paper
        order, keeping the output deterministic regardless of completion order.
        """
        section_names = ("introduction", "related_work", "method", "experiment")
        snapshot = {**state, "sections": dict(state.get("sections", {}))}
        tasks = [self.agents[name].run(snapshot) for name in section_names]
        results = await asyncio.gather(*tasks)
        state["sections"].update(dict(zip(section_names, results, strict=True)))
        state["current_step"] = "write_parallel_body_sections"

    async def _write_dependent_section(self, state: PaperState, section_name: str) -> None:
        """Generate a section after the prior phase has supplied its context."""
        state["sections"][section_name] = await self.agents[section_name].run(state)
        state["current_step"] = f"write_{section_name}"

    async def _quality_check(self, state: PaperState) -> Dict:
        """质量评估"""
        sections = state.get("sections", {})

        check_prompt = f"""
请评估以下论文各章节的质量（0-1分）：
- 结构完整性
- 逻辑连贯性
- 学术规范性
- 章节间一致性

各章节内容摘要：
{self._summarize_sections(sections)}

返回整体评分和具体反馈。
"""
        response = await self.llm.ainvoke(check_prompt)

        # 简化的质量判定逻辑
        return {
            "current_step": "quality_check",
            "feedback": [response.content],
        }

    async def _finalize(self, state: PaperState) -> Dict:
        """最终整合输出"""
        return {
            "is_complete": True,
            "current_step": "finalize",
        }

    # ==================== 路由函数 ====================

    def _route_after_outline(self, state: PaperState) -> str:
        """大纲生成后的路由判断"""
        if state.get("target_section"):
            return "single_section"
        return "full_paper"

    def _route_after_quality_check(self, state: PaperState) -> str:
        """质量检查后的路由 —— 通过或迭代"""
        # 简化：检查迭代次数
        feedback = state.get("feedback", [])
        if len(feedback) >= config.agent.max_iterations:
            return "pass"
        # 实际实现中应基于质量评分判断
        return "pass"

    # ==================== 辅助方法 ====================

    def _summarize_sections(self, sections: Dict) -> str:
        """生成章节摘要用于质量评估"""
        summaries = []
        for name, content in sections.items():
            text = content.get("content", "")[:200]
            summaries.append(f"[{name}]: {text}...")
        return "\n".join(summaries)

    # ==================== 公开接口 ====================

    async def run(self, user_input: str, **kwargs) -> PaperState:
        """执行论文写作工作流"""
        initial_state: PaperState = {
            "user_input": user_input,
            "paper_topic": kwargs.get("topic", user_input),
            "keywords": kwargs.get("keywords", []),
            "contributions": kwargs.get("contributions", []),
            "target_section": kwargs.get("target_section"),
            "language": kwargs.get("language", config.agent.language),
            "additional_context": kwargs.get("additional_context", ""),
            "paper_outline": "",
            "references": [],
            "symbol_table": {},
            "sections": {},
            "current_step": "init",
            "feedback": [],
            "error": None,
            "is_complete": False,
        }

        initial_state.update(await self._parse_input(initial_state))
        initial_state.update(await self._generate_outline(initial_state))

        if initial_state["target_section"]:
            target_section = str(initial_state["target_section"])
            if target_section not in self.agents:
                raise ValueError(f"Unsupported manuscript section: {target_section}")
            await self._write_dependent_section(initial_state, target_section)
        else:
            await self._write_independent_sections(initial_state)
            await self._write_dependent_section(initial_state, "conclusion")
            await self._write_dependent_section(initial_state, "abstract")

        initial_state.update(await self._quality_check(initial_state))
        initial_state.update(await self._finalize(initial_state))
        return initial_state
