"""
db.py - 轨迹数据 SQLite 存储层

使用 aiosqlite 提供异步数据库访问。
"""

import json
import aiosqlite
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "data" / "trajectories.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS trajectories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    sample_interval_ms INTEGER NOT NULL,
    total_frames INTEGER NOT NULL,
    duration_sec REAL NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trajectory_frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trajectory_id INTEGER NOT NULL REFERENCES trajectories(id) ON DELETE CASCADE,
    frame_index INTEGER NOT NULL,
    timestamp_ms REAL NOT NULL,
    left_arm TEXT NOT NULL,
    right_arm TEXT NOT NULL,
    left_hand TEXT NOT NULL,
    right_hand TEXT NOT NULL,
    UNIQUE(trajectory_id, frame_index)
);
"""


async def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.execute("PRAGMA foreign_keys = ON")
        await db.commit()


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    await db.execute("PRAGMA foreign_keys = ON")
    db.row_factory = aiosqlite.Row
    return db
