"""
tools/builtin/memory_tools.py — 记忆相关 Tool

提供记忆查询和写入的 Tool。
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState

logger = get_logger(__name__)


async def search_memory(state: AgentState, query: str = "", **kwargs) -> dict:
    """
    搜索用户的历史记忆（情节记忆 + 工作记忆）。

    TODO: 接入 EpisodicStore 向量检索
    """
    q = query or state.normalized_text
    logger.debug("search_memory: searching", query=q)

    # TODO: results = await episodic_store.search(q, top_k=5)
    results: list = []

    return {"memories": results, "query": q}


async def save_profile_fact(state: AgentState, fact: str = "", **kwargs) -> dict:
    """
    保存一个关于用户的稳定事实到用户画像。

    TODO: 接入 ProfileStore 写入
    """
    if not fact:
        return {"ok": False, "error": "fact cannot be empty"}

    logger.info("save_profile_fact: saving", user_id=state.user_id, fact=fact)

    # TODO: await profile_store.add_fact(state.user_id, fact)

    return {"ok": True, "fact": fact}
