"""
capabilities/memory/short_term.py — 工作记忆（短时记忆）

保存当前会话的最近 N 轮对话，存在进程内存中。
会话结束时自动清理。

存储结构：
  session_id → deque of {role, content, emotion, timestamp}
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any, Dict, List


class ShortTermMemory:
    """
    进程内工作记忆，使用 deque 限制最大轮数。
    线程安全（通过 dict 隔离 session）。
    """

    def __init__(self, max_turns: int = 15) -> None:
        self._max_turns = max_turns
        self._store: Dict[str, deque] = {}  # session_id → deque

    def append(
        self,
        session_id: str,
        role: str,
        content: str,
        emotion: str | None = None,
    ) -> None:
        """
        追加一条消息到指定 session 的工作记忆。

        Args:
            session_id: 会话 ID
            role:       'user' | 'assistant'
            content:    消息文本
            emotion:    情绪标签（可选，仅 user 消息有意义）
        """
        if session_id not in self._store:
            self._store[session_id] = deque(maxlen=self._max_turns * 2)  # 每轮含 user + assistant

        self._store[session_id].append({
            "role": role,
            "content": content,
            "emotion": emotion,
            "timestamp": datetime.utcnow().isoformat(),
        })

    def get(self, session_id: str) -> List[Dict[str, Any]]:
        """获取指定 session 的全部工作记忆（按时间顺序）"""
        return list(self._store.get(session_id, []))

    def clear(self, session_id: str) -> None:
        """清除指定 session 的工作记忆"""
        self._store.pop(session_id, None)

    def to_langchain_messages(self, session_id: str) -> list:
        """
        将工作记忆转换为 LangChain Message 格式，方便直接传给模型。

        TODO: 接入 langchain 消息类型
        """
        from langchain_core.messages import AIMessage, HumanMessage

        messages = []
        for item in self.get(session_id):
            if item["role"] == "user":
                messages.append(HumanMessage(content=item["content"]))
            elif item["role"] == "assistant":
                messages.append(AIMessage(content=item["content"]))
        return messages
