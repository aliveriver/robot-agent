"""
nodes/memory_recall.py - 记忆召回节点

负责从 SQLite 记忆存储中读取最近对话和相关记忆片段，
并将结果写回 `state.recent_messages` 与 `state.recalled_memories`。
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.memory.sqlite_store import get_memory_store
from src.robot_agent.graph.state import AgentState
from src.robot_agent.settings import settings

logger = get_logger(__name__)


async def memory_recall(state: AgentState) -> dict:
    """读取短期记忆和相关记忆片段。"""
    memory_store = get_memory_store()

    recent_messages = await memory_store.get_recent_messages(
        state.session_id,
        limit=settings.memory.short_term_max_turns * 2,
    )
    recalled_memories = await memory_store.search_memories(
        state.user_id,
        state.normalized_text,
        limit=5,
    )

    logger.debug(
        "memory_recall: done",
        session_id=state.session_id,
        recent_count=len(recent_messages),
        recalled_count=len(recalled_memories),
    )

    return {
        "recent_messages": recent_messages,
        "recalled_memories": recalled_memories,
    }
