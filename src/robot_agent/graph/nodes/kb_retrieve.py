"""
nodes/kb_retrieve.py — 知识库检索节点（可选）

职责：
- 判断是否需要知识库（用户问的是客观知识，而非个人话题）
- 若需要，对用户输入做向量检索
- 将 top-k 文档块存入 state.kb_chunks

知识库检索和记忆召回是不同的：
- 记忆 = 关于用户/会话的经历
- 知识库 = 关于客观知识/文档
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState

logger = get_logger(__name__)


async def kb_retrieve(state: AgentState) -> dict:
    """
    知识库检索节点（异步）。

    TODO: 实现真实检索逻辑
    1. 判断是否需要检索（可用 LLM router 或关键词规则）
    2. 调用 retriever.search(query, top_k)
    3. 返回文档块列表
    """
    query = state.normalized_text

    # ── 简单规则：如果 recalled_memories 已经足够，可以跳过 ──
    # 此处留给实现者决策是否检索知识库
    should_retrieve = True  # TODO: 替换为真实判断逻辑

    if not should_retrieve:
        logger.debug("kb_retrieve: skipped")
        return {}

    # TODO: chunks = await retriever.search(query, top_k=settings.knowledge.top_k)
    chunks: list = []

    logger.debug("kb_retrieve: retrieved", chunk_count=len(chunks))
    return {"kb_chunks": chunks}
