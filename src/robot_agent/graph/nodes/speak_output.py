"""
nodes/speak_output.py - 语音播报节点（空操作）

TTS 播放已在 response_gen 节点中以流式方式完成。
此节点保留为空操作，维持图拓扑不变。
"""

from __future__ import annotations

from src.robot_agent.graph.state import AgentState


async def speak_output(state: AgentState) -> dict:
    return {}
