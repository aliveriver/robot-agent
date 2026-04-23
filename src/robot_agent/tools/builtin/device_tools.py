"""
tools/builtin/device_tools.py — 设备信息类 Tool

提供基础设备状态查询工具。

每个 Tool 函数签名：
    async def tool_name(state: AgentState, **kwargs) -> dict
"""

from __future__ import annotations

from datetime import datetime

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState

logger = get_logger(__name__)


async def get_time(state: AgentState, **kwargs) -> dict:
    """获取当前时间，返回格式化字符串"""
    now = datetime.now()
    lang = state.language
    if lang == "cn":
        time_str = now.strftime("%Y年%m月%d日 %H时%M分")
    else:
        time_str = now.strftime("%A, %B %d, %Y at %I:%M %p")

    logger.debug("get_time: called", result=time_str)
    return {"time": time_str}


async def get_robot_status(state: AgentState, **kwargs) -> dict:
    """
    获取机器人当前状态。

    TODO: 接入真实设备状态查询
    """
    return {
        "wake_state": state.wake_state,
        "interrupted": state.interrupted,
        "session_id": state.session_id,
    }
