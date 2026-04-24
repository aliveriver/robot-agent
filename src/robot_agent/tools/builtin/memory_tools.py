"""
tools/builtin/memory_tools.py - 记忆相关 Tool

提供基于 SQLite 记忆存储的查询和写入能力，供 Tool 路由层调用。
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.memory.sqlite_store import get_memory_store
from src.robot_agent.graph.state import AgentState

logger = get_logger(__name__)


async def search_memory(state: AgentState, query: str = "", **kwargs) -> dict:
    """查询与当前用户相关的记忆片段。"""
    q = query or state.normalized_text
    logger.debug("search_memory: searching", query=q)

    results = await get_memory_store().search_memories(state.user_id, q, limit=5)
    return {"memories": results, "query": q}


async def save_profile_fact(state: AgentState, fact: str = "", **kwargs) -> dict:
    """保存一条用户画像事实。"""
    if not fact:
        return {"ok": False, "error": "fact cannot be empty"}

    logger.info("save_profile_fact: saving", user_id=state.user_id, fact=fact)
    await get_memory_store().save_profile_fact(state.user_id, fact, source="tool")
    return {"ok": True, "fact": fact}
