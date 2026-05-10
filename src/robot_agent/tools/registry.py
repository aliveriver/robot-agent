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
        from src.robot_agent.tools.builtin.arm_tools import (
            move_arm_joints,
            control_hand,
            control_both_hands,
            reset_arms,
            list_gestures,
            get_arm_status,
        )

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

        # ── 机械臂控制 ──────────────────────────────────────────────────────────
        self.register(
            "get_arm_status",
            get_arm_status,
            description=(
                "获取机器人当前双臂关节状态（位置/速度/力矩/温度）。"
                "建议在调用 move_arm_joints 前先执行本工具，了解当前姿态。"
            ),
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )
        self.register(
            "move_arm_joints",
            move_arm_joints,
            description=(
                "控制机器人左臂、右臂或双臂移动到指定关节角度。"
                "每侧臂有 7 个关节，按 J1~J7 顺序："
                "J1=肩俧仰(-1.57~+1.57)、J2=肩侧摇(-1.57~+1.57)、"
                "J3=肩旋转(-1.57~+1.57)、J4=肘弯曲(0~+2.36)、"
                "J5=腕旋转(-1.57~+1.57)、J6=腕俧仰(-1.04~+1.04)、J7=腕偏转(-0.79~+0.79)。"
                "常用姿态：垂侧=[0,0,0,0,0,0,0]、上抖=[1.57,0,0,0,0,0,0]、居中强=[0.3,0,0,1.0,0,0,0]。"
                "建议先调用 get_arm_status 确认当前关节位置，单次调整建议不超过 0.5 rad。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "side": {
                        "type": "string",
                        "enum": ["left", "right", "both"],
                        "description": "控制哪一侧手臂：left=左臂，right=右臂，both=双臂。",
                    },
                    "positions": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 7,
                        "maxItems": 7,
                        "description": (
                            "7 个关节目标角度（弧度），按 J1~J7 顺序提供。"
                            "各关节范围：J1(-1.57~1.57) J2(-1.57~1.57) J3(-1.57~1.57) "
                            "J4(0~2.36) J5(-1.57~1.57) J6(-1.04~1.04) J7(-0.79~0.79)。"
                        ),
                    },
                    "kp": {
                        "type": "number",
                        "description": "位置增益（可选，默认 100.0，慢速兴起可调小至 50.0）。",
                    },
                    "kd": {
                        "type": "number",
                        "description": "阻尼增益（可选，默认 2.0）。",
                    },
                },
                "required": ["side", "positions"],
                "additionalProperties": False,
            },
        )

        # ── 灵巧手控制 ──────────────────────────────────────────────────────────
        self.register(
            "control_hand",
            control_hand,
            description=(
                "控制单只灵巧手做预设手势或自定义角度。"
                "支持手势：open(张开)、close(握拳)、pinch(捏取)、point(指向)、"
                "thumbup(竖大拇指)、peace(剪刀手)、rock(摇滚)、ok(OK手势)、custom(自定义)。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "side": {
                        "type": "string",
                        "enum": ["left", "right"],
                        "description": "控制哪只手：left=左手，right=右手。",
                    },
                    "gesture": {
                        "type": "string",
                        "enum": [
                            "open", "close", "pinch", "point",
                            "thumbup", "peace", "rock", "ok", "custom",
                        ],
                        "description": "预设手势名称，或 custom 配合 angles 参数使用。",
                    },
                    "angles": {
                        "type": "array",
                        "items": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "minItems": 6,
                        "maxItems": 6,
                        "description": "仅 gesture=custom 时需要：6 个手指角度（0.0=张开，1.0=合拢）。",
                    },
                },
                "required": ["side", "gesture"],
                "additionalProperties": False,
            },
        )
        self.register(
            "control_both_hands",
            control_both_hands,
            description="同时控制左右双手，两只手可以做不同的手势。",
            parameters={
                "type": "object",
                "properties": {
                    "left_gesture": {
                        "type": "string",
                        "enum": [
                            "open", "close", "pinch", "point",
                            "thumbup", "peace", "rock", "ok", "custom",
                        ],
                        "description": "左手手势名称。",
                    },
                    "right_gesture": {
                        "type": "string",
                        "enum": [
                            "open", "close", "pinch", "point",
                            "thumbup", "peace", "rock", "ok", "custom",
                        ],
                        "description": "右手手势名称。",
                    },
                    "left_angles": {
                        "type": "array",
                        "items": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "minItems": 6,
                        "maxItems": 6,
                        "description": "左手自定义角度（left_gesture=custom 时使用）。",
                    },
                    "right_angles": {
                        "type": "array",
                        "items": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "minItems": 6,
                        "maxItems": 6,
                        "description": "右手自定义角度（right_gesture=custom 时使用）。",
                    },
                },
                "required": ["left_gesture", "right_gesture"],
                "additionalProperties": False,
            },
        )

        # ── 双臂辅助工具 ────────────────────────────────────────────────────────
        self.register(
            "reset_arms",
            reset_arms,
            description="让机器人双臂回到零位（关节清零）。",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )
        self.register(
            "list_gestures",
            list_gestures,
            description="列出当前灵巧手支持的所有预设手势名称。",
            parameters={
                "type": "object",
                "properties": {},
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
