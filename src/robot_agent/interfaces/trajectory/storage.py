"""使用 SQLite 在机器人本机持久化轨迹及元数据。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TrajectoryStore:
    """线程安全的轨迹存储；帧数据以 JSON 保存在 SQLite 中。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        default_path = Path.cwd() / "data" / "trajectories.sqlite3"
        self.path = Path(db_path or os.getenv("TRAJECTORY_DB_PATH", default_path))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trajectories (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    sample_interval REAL NOT NULL,
                    max_duration REAL NOT NULL,
                    duration REAL NOT NULL,
                    frame_count INTEGER NOT NULL,
                    frames_json TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def save(
        self,
        *,
        name: str,
        sample_interval: float,
        max_duration: float,
        duration: float,
        frames: list[dict[str, Any]],
    ) -> dict[str, Any]:
        trajectory_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO trajectories
                    (id, name, created_at, sample_interval, max_duration,
                     duration, frame_count, frames_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trajectory_id,
                    name,
                    created_at,
                    sample_interval,
                    max_duration,
                    duration,
                    len(frames),
                    json.dumps(frames, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            connection.commit()
        return self.get(trajectory_id, include_frames=False)

    def list(self) -> list[dict[str, Any]]:
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, name, created_at, sample_interval, max_duration,
                       duration, frame_count
                FROM trajectories ORDER BY created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, trajectory_id: str, *, include_frames: bool = True) -> dict[str, Any]:
        columns = "*" if include_frames else (
            "id, name, created_at, sample_interval, max_duration, duration, frame_count"
        )
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT {columns} FROM trajectories WHERE id = ?", (trajectory_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"trajectory not found: {trajectory_id}")
        result = dict(row)
        if include_frames:
            result["frames"] = json.loads(result.pop("frames_json"))
        return result

    def delete(self, trajectory_id: str) -> None:
        with self._lock, closing(self._connect()) as connection:
            cursor = connection.execute("DELETE FROM trajectories WHERE id = ?", (trajectory_id,))
            connection.commit()
        if cursor.rowcount == 0:
            raise KeyError(f"trajectory not found: {trajectory_id}")
