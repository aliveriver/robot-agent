"""
tools/registry.py - Tool 注册中心

统一维护工具函数及其元数据，供两条链路复用：

1. `tool_execute`：按名字查函数并执行
2. `tool_route`：获取工具 schema，交给 LLM 做 tool calling

工具函数约定:
    async def tool_name(state: AgentState, **kwargs) -> dict
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.robot_agent.bootstrap.logging import get_logger

logger = get_logger(__name__)

ToolFn = Callable[..., Any]


@dataclass
class ToolSpec:
    """保存工具函数与 LLM tool calling 所需元数据。"""

    fn: ToolFn
    description: str
    parameters: dict[str, Any]


class ToolRegistry:
    """进程级单例工具注册中心。"""

    _instance: "ToolRegistry | None" = None

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    @classmethod
    def get_instance(cls) -> "ToolRegistry":
        """获取进程级单例，并在首次访问时注册内置工具。"""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._register_builtins()
        return cls._instance

    def _register_builtins(self) -> None:
        """注册项目内置工具。"""
        from src.robot_agent.tools.builtin.control_tools import sleep_robot, start_voice_clone
        from src.robot_agent.tools.builtin.device_tools import get_time, get_robot_status
        from src.robot_agent.tools.builtin.memory_tools import save_profile_fact, search_memory

        self.register(
            "get_time",
            get_time,
            description="获取机器人当前的本地时间。",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )
        self.register(
            "get_robot_status",
            get_robot_status,
            description="获取机器人运行状态，包括唤醒状态。",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )
        self.register(
            "search_memory",
            search_memory,
            description="搜索与用户查询相关的存储记忆。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "记忆搜索查询词。",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        )
        self.register(
            "save_profile_fact",
            save_profile_fact,
            description="将稳定的用户特征事实保存到长期记忆中。",
            parameters={
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "要保存的用户特征事实。",
                    }
                },
                "required": ["fact"],
                "additionalProperties": False,
            },
        )
        self.register(
            "sleep_robot",
            sleep_robot,
            description="当用户明确要求休息时，使机器人进入休眠模式。",
            parameters={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "机器人进入休眠的原因。",
                    }
                },
                "additionalProperties": False,
            },
        )
        self.register(
            "start_voice_clone",
            start_voice_clone,
            description="通过录制并上传语音样本开始声音克隆流程。",
            parameters={
                "type": "object",
                "properties": {
                    "duration": {
                        "type": "integer",
                        "description": "录音时长（秒）。",
                        "minimum": 3,
                        "maximum": 30,
                    }
                },
                "additionalProperties": False,
            },
        )

        logger.info("ToolRegistry: builtins registered", count=len(self._tools))

    def register(
        self,
        name: str,
        fn: ToolFn,
        description: str = "",
        parameters: dict[str, Any] | None = None,
    ) -> None:
        """注册一个工具及其 schema 元数据。"""
        if name in self._tools:
            logger.warning("ToolRegistry: overwriting existing tool", name=name)

        self._tools[name] = ToolSpec(
            fn=fn,
            description=description or name,
            parameters=parameters
            or {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )
        logger.debug("ToolRegistry: registered", name=name)

    def get(self, name: str) -> ToolFn | None:
        """按名字获取工具函数。"""
        spec = self._tools.get(name)
        return spec.fn if spec else None

    def list_tools(self) -> list[str]:
        """列出当前已注册的工具名。"""
        return list(self._tools.keys())

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """返回可直接交给 ChatOpenAI.bind_tools 的 schema 列表。"""
        schemas: list[dict[str, Any]] = []
        for name, spec in self._tools.items():
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": spec.description,
                        "parameters": spec.parameters,
                    },
                }
            )
        return schemas
