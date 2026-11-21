from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from langchain_core.messages import AIMessage


class OfflineResearchModel:
    """Deterministic query planner used when no platform LLM key is configured."""

    def with_structured_output(self, schema: Any):
        class StructuredResponse:
            def invoke(self, messages: list[Any]):
                user_text = str(messages[-1].content)
                current_year = datetime.now().year
                years = [int(value) for value in re.findall(r"(?:19|20)\d{2}", user_text)]
                relative = re.search(r"(?:近|过去|最近)\s*(\d{1,2})\s*年", user_text)
                start_year = min(years) if years else current_year - (int(relative.group(1)) if relative else 5)
                end_year = max(years) if years else current_year
                keywords = extract_keywords(user_text)
                english = next((item for item in keywords if re.search(r"[A-Za-z]", item)), keywords[0])
                queries = list(
                    dict.fromkeys(
                        [
                            keywords[0],
                            english,
                            f"{english} methods techniques",
                            f"{english} applications use cases",
                            f"{english} survey systematic review",
                        ]
                    )
                )
                while len(queries) < 5:
                    queries.append(f"{english} related research {len(queries) + 1}")
                return schema(
                    intent_summary=f"围绕“{user_text}”检索代表性研究、方法、应用与综述文献。",
                    keywords=keywords[:12],
                    start_year=start_year,
                    end_year=end_year,
                    queries=queries[:5],
                )

        return StructuredResponse()

    def invoke(self, messages: list[Any]) -> AIMessage:
        prompt = str(messages[-1].content)
        papers = parse_papers(prompt)
        original_query = prompt_value(prompt, "用户原始需求") or "当前研究问题"
        lines = [
            "# 文献检索报告",
            "",
            "## 检索概述",
            "",
            f"本报告围绕“{original_query}”生成，并基于本次检索结果整理研究脉络。",
            "",
            "## 重点文献",
            "",
        ]
        if not papers:
            lines.append("当前数据源未返回满足条件且可核验全文的文献。")
        for index, paper in enumerate(papers, start=1):
            lines.extend(
                [
                    f"### [{index}] {paper.get('title') or '未命名文献'}",
                    "",
                    f"- 作者：{'、'.join(paper.get('authors') or []) or '作者信息缺失'}",
                    f"- 年份：{paper.get('year') or '未知'}",
                    f"- 来源：{paper.get('venue') or '未知'}",
                    "",
                ]
            )
        return AIMessage(content="\n".join(lines))


def extract_keywords(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}", text)
    return list(dict.fromkeys(tokens))[:12] or [text]


def prompt_value(prompt: str, label: str) -> str | None:
    match = re.search(rf"{re.escape(label)}：([^\n]+)", prompt)
    return match.group(1).strip() if match else None


def parse_papers(prompt: str) -> list[dict[str, Any]]:
    marker = "候选文献 JSON："
    if marker not in prompt:
        return []
    try:
        value = json.loads(prompt.split(marker, 1)[1].strip())
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []
