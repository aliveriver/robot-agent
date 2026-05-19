"""
player.py - 轨迹回放引擎

从 SQLite 读取帧序列，按时间间隔逐帧发送位置指令。
"""

import asyncio
import json
from typing import Optional, Callable

from .db import get_db
from .ros_bridge import BodySnapshot


class Player:
    def __init__(self, bridge, on_progress: Optional[Callable] = None):
        self.bridge = bridge
        self.on_progress = on_progress
        self.playing = False
        self.progress = 0.0
        self._task: Optional[asyncio.Task] = None

    async def play(self, trajectory_id: int, speed: float = 1.0):
        if self.playing:
            raise RuntimeError("已在回放中")

        frames = await self._load_frames(trajectory_id)
        if not frames:
            raise ValueError("轨迹无帧数据")

        self.playing = True
        self.progress = 0.0
        self._task = asyncio.ensure_future(self._play_loop(frames, speed))

    async def stop(self):
        if not self.playing:
            return
        self.playing = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _play_loop(self, frames: list[dict], speed: float):
        try:
            total = len(frames)
            for i, frame in enumerate(frames):
                if not self.playing:
                    break

                snapshot = BodySnapshot(
                    left_arm=frame["left_arm"],
                    right_arm=frame["right_arm"],
                    left_hand=frame["left_hand"],
                    right_hand=frame["right_hand"],
                )
                self.bridge.send_frame(snapshot)

                self.progress = (i + 1) / total
                if self.on_progress:
                    self.on_progress(self.progress)

                if i < total - 1:
                    dt = (frames[i + 1]["timestamp_ms"] - frame["timestamp_ms"]) / 1000.0
                    await asyncio.sleep(max(0.01, dt / speed))

            self.playing = False
            self.progress = 1.0
            if self.on_progress:
                self.on_progress(1.0)
        except asyncio.CancelledError:
            pass
        finally:
            self.playing = False

    async def _load_frames(self, trajectory_id: int) -> list[dict]:
        db = await get_db()
        try:
            cursor = await db.execute(
                """SELECT frame_index, timestamp_ms, left_arm, right_arm, left_hand, right_hand
                   FROM trajectory_frames WHERE trajectory_id = ? ORDER BY frame_index""",
                (trajectory_id,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "frame_index": row[0],
                    "timestamp_ms": row[1],
                    "left_arm": json.loads(row[2]),
                    "right_arm": json.loads(row[3]),
                    "left_hand": json.loads(row[4]),
                    "right_hand": json.loads(row[5]),
                }
                for row in rows
            ]
        finally:
            await db.close()
