"""
nodes/tool_route.py - Tool 路由节点

负责决定本轮输入是否需要调用工具，分两层处理：

1. 硬路由：对明确关键词直接映射到指定工具
2. LLM tool calling：其余情况交给模型基于工具 schema 做选择

输出写回 `state.tool_requests`，格式为:
    [{"tool": "tool_name", "args": {...}}]
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.llm.chat_model import get_chat_model
from src.robot_agent.graph.state import AgentState
from src.robot_agent.tools.registry import ToolRegistry

logger = get_logger(__name__)

HARD_ROUTE_CN: dict[str, str] = {
    "克隆声音": "start_voice_clone",
    "声音克隆": "start_voice_clone",
    "休眠": "sleep_robot",
    "睡觉": "sleep_robot",
    "几点": "get_time",
    "时间": "get_time",
}

HARD_ROUTE_EN: dict[str, str] = {
    "clone voice": "start_voice_clone",
    "what time": "get_time",
    "sleep": "sleep_robot",
}


def _hard_route(text: str, lang: str) -> str | None:
    """基于关键词做低成本硬路由。"""
    route_map = HARD_ROUTE_CN if lang == "cn" else HARD_ROUTE_EN
    lowered = text.lower()
    for keyword, tool_name in route_map.items():
        if keyword in lowered:
            return tool_name
    return None


def _extract_tool_requests(reply: Any) -> list[dict[str, Any]]:
    """从模型返回中提取 tool calls。"""
    tool_calls = getattr(reply, "tool_calls", None) or []
    requests: list[dict[str, Any]] = []

    for tool_call in tool_calls:
        name = tool_call.get("name")
        args = tool_call.get("args", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        if name:
            requests.append({"tool": name, "args": args})

    return requests


async def tool_route(state: AgentState) -> dict:
    """为当前输入选择要执行的工具。"""
    text = state.normalized_text.strip()
    if not text:
        return {"tool_requests": []}

    hard_tool = _hard_route(text, state.language)
    if hard_tool:
        logger.info("tool_route: hard route matched", tool=hard_tool)
        return {"tool_requests": [{"tool": hard_tool, "args": {}}]}

    registry = ToolRegistry.get_instance()
    tool_schemas = registry.get_tool_schemas()

    system_prompt = (
        "You are a conservative tool router for a voice robot. "
        "Only call a tool when the user's request clearly needs a tool action. "
        "If normal conversation is sufficient, do not call any tool."
    )
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=text),
    ]

    try:
        model = get_chat_model(multimodal=False).bind_tools(tool_schemas)
        reply = await model.ainvoke(messages)
        tool_requests = _extract_tool_requests(reply)
    except Exception as exc:
        logger.warning("tool_route: LLM tool routing failed", error=str(exc))
        tool_requests = []

    if tool_requests:
        logger.info("tool_route: llm selected tools", tools=[req["tool"] for req in tool_requests])
    else:
        logger.debug("tool_route: no tool selected")

    return {"tool_requests": tool_requests}
