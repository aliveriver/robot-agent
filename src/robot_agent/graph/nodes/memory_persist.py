"""
nodes/memory_persist.py — 记忆写入节点

职责：
- 将本轮对话写入 messages 表（每轮都写）
- 判断是否需要生成会话摘要（每 N 轮触发一次）
- 判断是否需要提取长期记忆（情节记忆 / 用户画像）

写入策略：
- messages：每轮必写
- session_summary：每 N 轮触发 summarizer
- episodic/profile：满足重要性阈值时才写
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState
from src.robot_agent.settings import settings

logger = get_logger(__name__)


async def memory_persist(state: AgentState) -> dict:
    """
    记忆持久化节点（异步）。

    TODO: 注入 memory service 依赖
    """
    session_id = state.session_id
    user_id = state.user_id

    # ── 1. 写入原始消息 ──────────────────────────────────────
    # TODO: await message_store.append(session_id, role="user", content=state.normalized_text, emotion=state.emotion)
    # TODO: await message_store.append(session_id, role="assistant", content=state.response_text)
    logger.debug("memory_persist: message logged", session_id=session_id)

    # ── 2. 判断是否触发摘要 ──────────────────────────────────
    # turn_count = len(state.recent_messages)
    # every_n = settings.memory.summary_every_n_turns
    # if turn_count > 0 and turn_count % every_n == 0:
    #     TODO: await summarizer.run(session_id)
    #     logger.info("memory_persist: summary triggered", turn=turn_count)

    # ── 3. 判断是否提取长期记忆 ─────────────────────────────
    # TODO: result = await memory_extract_chain.run(state.recent_messages)
    # if result.should_extract and result.importance >= settings.memory.episodic_importance_threshold:
    #     await episodic_store.save(user_id, result)

    return {}
