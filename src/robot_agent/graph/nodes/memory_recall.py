"""
nodes/memory_recall.py — 记忆召回节点

职责：
- 从短时记忆（进程内）读取最近 N 轮对话
- 从用户画像读取基本信息
- （可选）从情节记忆向量库检索相关片段

实现时替换 TODO 注释部分。
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState

logger = get_logger(__name__)


async def memory_recall(state: AgentState) -> dict:
    """
    记忆召回节点（异步）。

    从以下位置读取记忆：
    1. ShortTermMemory（进程内最近 N 轮对话）
    2. UserProfile（用户基本信息）
    3. EpisodicMemory（向量检索，可选）

    TODO: 注入 memory service 依赖（通过 DI 容器或全局单例）
    """
    session_id = state.session_id
    user_id = state.user_id
    query = state.normalized_text

    # ── 1. 短时记忆 ───────────────────────────────────────────
    # TODO: recent_messages = await short_term_memory.get(session_id)
    recent_messages: list = []

    # ── 2. 用户画像 ───────────────────────────────────────────
    # TODO: profile = await profile_store.get(user_id)
    # recent_messages 可以在构建 prompt 时插入 profile 信息

    # ── 3. 情节记忆检索（语义相似度）────────────────────────
    # TODO: recalled = await episodic_store.search(query, top_k=3)
    recalled_memories: list = []

    logger.debug(
        "memory_recall: done",
        session_id=session_id,
        recent_count=len(recent_messages),
        recalled_count=len(recalled_memories),
    )

    return {
        "recent_messages": recent_messages,
        "recalled_memories": recalled_memories,
    }
