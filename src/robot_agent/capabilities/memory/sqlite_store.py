"""
capabilities/memory/sqlite_store.py - SQLite 记忆存储服务

封装基于 SQLite 的消息记录和用户事实存储，负责：
1. 初始化消息表和用户事实表
2. 持久化用户/助手对话消息
3. 读取最近对话，作为短期记忆输入 prompt
4. 保存和检索用户事实，作为长期记忆补充

主要接口：
- `get_memory_store()`：返回进程级 SQLite 记忆服务单例
- `SQLiteMemoryStore.append_message(...)`：追加一条对话消息
- `SQLiteMemoryStore.get_recent_messages(...)`：读取最近若干条消息
- `SQLiteMemoryStore.save_profile_fact(...)`：保存一条用户事实
- `SQLiteMemoryStore.search_memories(...)`：搜索可注入 prompt 的记忆片段
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite

from src.robot_agent.settings import ROOT_DIR, settings


class SQLiteMemoryStore:
    """SQLite 记忆存储服务。"""

    def __init__(self, sqlite_path: str) -> None:
        self._db_path = Path(sqlite_path)
        if not self._db_path.is_absolute():
            self._db_path = ROOT_DIR / self._db_path

        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """初始化数据库和表结构。"""
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return

            self._db_path.parent.mkdir(parents=True, exist_ok=True)

            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        emotion TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_messages_session_id_id
                    ON messages(session_id, id DESC)
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_messages_user_id_id
                    ON messages(user_id, id DESC)
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS profile_facts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        fact TEXT NOT NULL,
                        source TEXT NOT NULL DEFAULT 'tool',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_profile_facts_user_id_id
                    ON profile_facts(user_id, id DESC)
                    """
                )
                await db.commit()

            self._initialized = True

    async def append_message(
        self,
        session_id: str,
        user_id: str,
        role: str,
        content: str,
        emotion: str | None = None,
    ) -> None:
        """写入一条消息记录。"""
        await self.initialize()

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO messages (session_id, user_id, role, content, emotion)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, user_id, role, content, emotion),
            )
            await db.commit()

    async def get_recent_messages(self, session_id: str, limit: int) -> list[dict[str, Any]]:
        """读取指定会话最近若干条消息。"""
        await self.initialize()

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT role, content, emotion, created_at
                FROM messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            )
            rows = await cursor.fetchall()

        messages = [dict(row) for row in rows]
        messages.reverse()
        return messages

    async def save_profile_fact(
        self,
        user_id: str,
        fact: str,
        source: str = "tool",
    ) -> None:
        """保存一条用户事实。"""
        await self.initialize()

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO profile_facts (user_id, fact, source)
                VALUES (?, ?, ?)
                """,
                (user_id, fact, source),
            )
            await db.commit()

    async def search_memories(self, user_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """搜索用户相关记忆，返回统一的记忆片段结构。"""
        await self.initialize()

        normalized_query = query.strip()
        like_query = f"%{normalized_query}%"

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row

            if normalized_query:
                facts_cursor = await db.execute(
                    """
                    SELECT fact, source, created_at
                    FROM profile_facts
                    WHERE user_id = ? AND fact LIKE ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, like_query, limit),
                )
                messages_cursor = await db.execute(
                    """
                    SELECT content, role, session_id, created_at
                    FROM messages
                    WHERE user_id = ? AND content LIKE ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, like_query, limit),
                )
            else:
                facts_cursor = await db.execute(
                    """
                    SELECT fact, source, created_at
                    FROM profile_facts
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                )
                messages_cursor = await db.execute(
                    """
                    SELECT content, role, session_id, created_at
                    FROM messages
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                )

            fact_rows = await facts_cursor.fetchall()
            message_rows = await messages_cursor.fetchall()

        memories: list[dict[str, Any]] = []

        for row in fact_rows:
            memories.append(
                {
                    "memory_text": row["fact"],
                    "memory_type": "profile",
                    "source": row["source"],
                    "created_at": row["created_at"],
                }
            )

        for row in message_rows:
            memories.append(
                {
                    "memory_text": row["content"],
                    "memory_type": "message",
                    "source": row["role"],
                    "session_id": row["session_id"],
                    "created_at": row["created_at"],
                }
            )

        return memories[:limit]


_memory_store: SQLiteMemoryStore | None = None


def get_memory_store() -> SQLiteMemoryStore:
    """返回进程级 SQLite 记忆服务单例。"""
    global _memory_store

    if _memory_store is None:
        _memory_store = SQLiteMemoryStore(settings.database.sqlite_path)
    return _memory_store
