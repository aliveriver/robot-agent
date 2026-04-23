"""
tools/registry.py — Tool 注册表

统一管理所有可用 Tool 的注册与查找。
Tool 实现函数签名：async def tool_fn(state: AgentState, **kwargs) -> dict

用法:
    registry = ToolRegistry.get_instance()
    registry.register("get_time", get_time_tool)
    fn = registry.get("get_time")
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from src.robot_agent.bootstrap.logging import get_logger

logger = get_logger(__name__)

# Tool 函数类型
ToolFn = Callable[..., Any]


class ToolRegistry:
    """
    Tool 注册表单例。
    维护 tool_name → tool_function 的映射。
    """

    _instance: Optional["ToolRegistry"] = None

    def __init__(self) -> None:
        self._tools: Dict[str, ToolFn] = {}

    @classmethod
    def get_instance(cls) -> "ToolRegistry":
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._register_builtins()
        return cls._instance

    def _register_builtins(self) -> None:
        """注册内置 Tool（在此扩展）"""
        from src.robot_agent.tools.builtin.device_tools import get_time, get_robot_status
        from src.robot_agent.tools.builtin.memory_tools import search_memory, save_profile_fact

        self.register("get_time", get_time)
        self.register("get_robot_status", get_robot_status)
        self.register("search_memory", search_memory)
        self.register("save_profile_fact", save_profile_fact)

        logger.info("ToolRegistry: builtins registered", count=len(self._tools))

    def register(self, name: str, fn: ToolFn) -> None:
        """注册一个 Tool"""
        if name in self._tools:
            logger.warning("ToolRegistry: overwriting existing tool", name=name)
        self._tools[name] = fn
        logger.debug("ToolRegistry: registered", name=name)

    def get(self, name: str) -> ToolFn | None:
        """按名称查找 Tool，不存在返回 None"""
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        """列出所有已注册的 Tool 名称"""
        return list(self._tools.keys())
