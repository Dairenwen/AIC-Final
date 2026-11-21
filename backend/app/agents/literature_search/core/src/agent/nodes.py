from __future__ import annotations

from langchain_core.tools import BaseTool

from .state import ToolAgentState


class ToolNodes:
    def __init__(self, tools: list[BaseTool]) -> None:
        self.tools = {tool.name: tool for tool in tools}

    def invoke_tool(self, state: ToolAgentState) -> ToolAgentState:
        tool_name = state["tool_name"]
        tool = self.tools.get(tool_name)
        if tool is None:
            return {"error": f"Unknown tool: {tool_name}"}
        try:
            return {"result": tool.invoke(state.get("tool_input", {})), "error": None}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
