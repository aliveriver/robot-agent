"""
nodes/tool_execute.py — Tool 执行节点

职责：
- 读取 state.tool_requests
- 从 ToolRegistry 查找对应 Tool 实现
- 依次执行，收集结果到 state.tool_results

每个 Tool 返回统一结构：
{
    "ok": bool,
    "tool": str,
    "data": dict | None,
    "error": str | None,
}
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState
from src.robot_agent.tools.registry import ToolRegistry

logger = get_logger(__name__)


async def tool_execute(state: AgentState) -> dict:
    """
    Tool 执行节点（异步）。

    遍历 tool_requests，逐个执行，超时或异常时记录错误而非崩溃。
    """
    if not state.tool_requests:
        return {}

    registry = ToolRegistry.get_instance()
    results = []

    for req in state.tool_requests:
        tool_name = req.get("tool", "")
        args = req.get("args", {})

        tool_fn = registry.get(tool_name)
        if tool_fn is None:
            logger.warning("tool_execute: tool not found", tool=tool_name)
            results.append({"ok": False, "tool": tool_name, "data": None, "error": "tool not found"})
            continue

        try:
            logger.info("tool_execute: running tool", tool=tool_name, args=args)
            result = await tool_fn(state=state, **args)
            results.append({"ok": True, "tool": tool_name, "data": result, "error": None})
        except Exception as exc:
            logger.error("tool_execute: tool failed", tool=tool_name, error=str(exc))
            results.append({"ok": False, "tool": tool_name, "data": None, "error": str(exc)})

    return {"tool_results": results}
