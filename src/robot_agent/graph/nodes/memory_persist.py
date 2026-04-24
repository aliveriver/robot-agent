"""
nodes/memory_persist.py - 记忆写入节点

负责在图执行完成后将本轮用户输入和助手回复写入 SQLite，
为后续短期记忆召回和记忆检索提供持久化数据。
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.memory.sqlite_store import get_memory_store
from src.robot_agent.graph.state import AgentState

logger = get_logger(__name__)


async def memory_persist(state: AgentState) -> dict:
    """将本轮消息写入 SQLite。"""
    memory_store = get_memory_store()

    if state.normalized_text:
        await memory_store.append_message(
            session_id=state.session_id,
            user_id=state.user_id,
            role="user",
            content=state.normalized_text,
            emotion=state.emotion,
        )

    if state.response_text and state.response_text not in {"__SKIP__", "__STOP__", "__EXIT__"}:
        await memory_store.append_message(
            session_id=state.session_id,
            user_id=state.user_id,
            role="assistant",
            content=state.response_text,
        )

    logger.debug("memory_persist: message logged", session_id=state.session_id)
    return {}
