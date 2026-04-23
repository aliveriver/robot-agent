"""
nodes/tool_route.py — Tool 路由节点

职责：
- 检查用户输入是否需要调用 Tool
- 分两层路由：
  1. 硬规则路由：关键词直接匹配 → 固定 Tool（不过 LLM，延迟低）
  2. LLM 自主选择：其他情况让模型决定是否/调用哪个 Tool

实现时将 hard_route_map 扩充，并接入真实 LLM tool calling。
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState

logger = get_logger(__name__)

# ── 硬规则路由表：关键词 → tool name ────────────────────────
# 这些指令直接匹配，不走 LLM，保证实时响应
HARD_ROUTE_CN: dict[str, str] = {
    "克隆声音": "start_voice_clone",
    "声音克隆": "start_voice_clone",
    "休眠": "sleep_robot",
    "睡觉": "sleep_robot",
    "时间": "get_time",
    "几点": "get_time",
}

HARD_ROUTE_EN: dict[str, str] = {
    "clone voice": "start_voice_clone",
    "what time": "get_time",
    "sleep": "sleep_robot",
}


def _hard_route(text: str, lang: str) -> str | None:
    """硬规则匹配，返回 tool name 或 None"""
    route_map = HARD_ROUTE_CN if lang == "cn" else HARD_ROUTE_EN
    for keyword, tool_name in route_map.items():
        if keyword in text.lower():
            return tool_name
    return None


async def tool_route(state: AgentState) -> dict:
    """
    Tool 路由节点（异步）。

    优先硬规则 → 再 LLM tool calling。
    结果写入 state.tool_requests（list of {tool: str, args: dict}）。
    """
    text = state.normalized_text

    # ── 1. 硬规则路由 ────────────────────────────────────────
    hard_tool = _hard_route(text, state.language)
    if hard_tool:
        logger.info("tool_route: hard route matched", tool=hard_tool)
        return {"tool_requests": [{"tool": hard_tool, "args": {}}]}

    # ── 2. LLM 自主 Tool Calling ─────────────────────────────
    # TODO: 调用 LLM with tools=registry.get_tool_schemas()
    # TODO: 解析 LLM 返回的 tool_calls，写入 tool_requests
    tool_requests: list = []

    logger.debug("tool_route: no tool selected")
    return {"tool_requests": tool_requests}
