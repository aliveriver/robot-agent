"""
nodes/tool_execute.py - Tool 执行节点

负责按顺序执行 `state.tool_requests` 中的工具，并收集两个输出：

1. `tool_results`：工具执行结果，供后续 LLM 参考
2. `state_updates`：工具显式要求回写到图状态的字段

工具如果返回:
    {"state_updates": {"wake_state": "sleep", "response_text": "..."}}
则这些字段会直接写回图状态。
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState
from src.robot_agent.tools.registry import ToolRegistry

logger = get_logger(__name__)


async def tool_execute(state: AgentState) -> dict:
    """执行当前轮次请求的工具列表。"""
    if not state.tool_requests:
        return {}

    registry = ToolRegistry.get_instance()
    results: list[dict] = []
    state_updates: dict = {}

    for req in state.tool_requests:
        tool_name = req.get("tool", "")
        args = req.get("args", {})

        tool_fn = registry.get(tool_name)
        if tool_fn is None:
            logger.warning("tool_execute: tool not found", tool=tool_name)
            results.append(
                {
                    "ok": False,
                    "tool": tool_name,
                    "data": None,
                    "error": "tool not found",
                }
            )
            continue

        try:
            logger.info("tool_execute: running tool", tool=tool_name, args=args)
            result = await tool_fn(state=state, **args)

            if isinstance(result, dict):
                updates = result.get("state_updates", {})
                if isinstance(updates, dict):
                    state_updates.update(updates)

            results.append(
                {
                    "ok": True,
                    "tool": tool_name,
                    "data": result,
                    "error": None,
                }
            )
        except Exception as exc:
            logger.error("tool_execute: tool failed", tool=tool_name, error=str(exc))
            results.append(
                {
                    "ok": False,
                    "tool": tool_name,
                    "data": None,
                    "error": str(exc),
                }
            )

    return {
        "tool_results": results,
        **state_updates,
    }
