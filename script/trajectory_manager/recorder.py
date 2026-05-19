"""
recorder.py - 轨迹录制引擎

按指定间隔采样机器人全身状态，暂存内存，停止后批量写入 SQLite。
"""

import asyncio
import json
import time
from typing import Optional, Callable

from .db import get_db
from .ros_bridge import BodySnapshot


class Recorder:
    def __init__(self, bridge, on_frame: Optional[Callable] = None):
        self.bridge = bridge
        self.on_frame = on_frame
        self.recording = False
        self.frames: list[dict] = []
        self.name: str = ""
        self.interval_ms: int = 100
        self._task: Optional[asyncio.Task] = None
        self._start_time: float = 0.0

    @property
    def elapsed_sec(self) -> float:
        if not self.recording:
            return 0.0
        return time.time() - self._start_time

    def start(self, name: str, interval_ms: int = 100):
        if self.recording:
            raise RuntimeError("已在录制中")
        self.name = name
        self.interval_ms = interval_ms
        self.frames = []
        self.recording = True
        self._start_time = time.time()
        self._task = asyncio.ensure_future(self._record_loop())

    async def stop(self) -> int:
        if not self.recording:
            return 0
        self.recording = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        trajectory_id = await self._save()
        return trajectory_id

    async def _record_loop(self):
        interval = self.interval_ms / 1000.0
        try:
            while self.recording:
                snapshot = self.bridge.get_snapshot()
                frame = {
                    "frame_index": len(self.frames),
                    "timestamp_ms": (time.time() - self._start_time) * 1000.0,
                    "left_arm": snapshot.left_arm[:],
                    "right_arm": snapshot.right_arm[:],
                    "left_hand": snapshot.left_hand[:],
                    "right_hand": snapshot.right_hand[:],
                }
                self.frames.append(frame)
                if self.on_frame:
                    self.on_frame(len(self.frames), self.elapsed_sec)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def _save(self) -> int:
        if not self.frames:
            return 0
        duration = self.frames[-1]["timestamp_ms"] / 1000.0
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO trajectories (name, description, sample_interval_ms, total_frames, duration_sec)
                   VALUES (?, '', ?, ?, ?)""",
                (self.name, self.interval_ms, len(self.frames), duration),
            )
            await db.commit()
            cursor = await db.execute("SELECT last_insert_rowid()")
            row = await cursor.fetchone()
            trajectory_id = row[0]

            await db.executemany(
                """INSERT INTO trajectory_frames
                   (trajectory_id, frame_index, timestamp_ms, left_arm, right_arm, left_hand, right_hand)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        trajectory_id,
                        f["frame_index"],
                        f["timestamp_ms"],
                        json.dumps(f["left_arm"]),
                        json.dumps(f["right_arm"]),
                        json.dumps(f["left_hand"]),
                        json.dumps(f["right_hand"]),
                    )
                    for f in self.frames
                ],
            )
            await db.commit()
            return trajectory_id
        finally:
            await db.close()
