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
        self.paused = False
        self.progress = 0.0
        self.current_loop = 0
        self.total_loops = 1
        self._task: Optional[asyncio.Task] = None

    async def play(self, trajectory_id: int, speed: float = 1.0,
                   repeat: int = 1, interval_sec: float = 0.0):
        """
        repeat: 重复次数，0 表示无限循环
        interval_sec: 每次重复之间的间隔（秒）
        """
        if self.playing:
            raise RuntimeError("已在回放中")

        frames = await self._load_frames(trajectory_id)
        if not frames:
            raise ValueError("轨迹无帧数据")

        self.playing = True
        self.paused = False
        self.progress = 0.0
        self.current_loop = 0
        self.total_loops = repeat
        self._task = asyncio.ensure_future(
            self._play_loop(frames, speed, repeat, interval_sec)
        )

    async def stop(self):
        if not self.playing:
            return
        self.playing = False
        self.paused = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def pause(self):
        if not self.playing:
            raise RuntimeError("当前没有在回放")
        self.paused = True

    async def resume(self):
        if not self.playing:
            raise RuntimeError("当前没有在回放")
        self.paused = False

    async def _wait_if_paused(self):
        while self.playing and self.paused:
            await asyncio.sleep(0.05)

    async def _sleep_with_pause(self, duration: float):
        remaining = max(0.0, duration)
        while self.playing and remaining > 0:
            await self._wait_if_paused()
            if not self.playing:
                return
            step = min(0.02, remaining)
            await asyncio.sleep(step)
            remaining -= step

    async def _play_loop(self, frames: list[dict], speed: float,
                         repeat: int, interval_sec: float):
        try:
            total = len(frames)
            loop_count = 0

            while True:
                loop_count += 1
                self.current_loop = loop_count

                prev_snapshot = None
                for i, frame in enumerate(frames):
                    if not self.playing:
                        return
                    await self._wait_if_paused()
                    if not self.playing:
                        return

                    snapshot = BodySnapshot(
                        left_arm=frame["left_arm"],
                        right_arm=frame["right_arm"],
                        left_hand=frame["left_hand"],
                        right_hand=frame["right_hand"],
                    )

                    # 计算帧间隔，用于速度自适应
                    if i < total - 1:
                        dt = (frames[i + 1]["timestamp_ms"] - frame["timestamp_ms"]) / 1000.0
                    elif i > 0:
                        dt = (frame["timestamp_ms"] - frames[i - 1]["timestamp_ms"]) / 1000.0
                    else:
                        dt = 0.1
                    actual_dt = max(0.01, dt / speed)

                    self.bridge.send_frame(snapshot, dt=actual_dt, prev_snapshot=prev_snapshot)
                    prev_snapshot = snapshot

                    self.progress = (i + 1) / total
                    if self.on_progress:
                        self.on_progress(self.progress)

                    if i < total - 1:
                        await self._sleep_with_pause(actual_dt)

                # repeat=0 无限循环，否则到达次数后停止
                if repeat != 0 and loop_count >= repeat:
                    break

                # 循环间隔等待
                if interval_sec > 0 and self.playing:
                    await self._sleep_with_pause(interval_sec)

            self.progress = 1.0
            if self.on_progress:
                self.on_progress(1.0)
        except asyncio.CancelledError:
            pass
        finally:
            self.playing = False
            self.paused = False

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
