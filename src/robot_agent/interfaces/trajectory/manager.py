"""轨迹录制/回放状态机，保证同一时间只执行一个危险动作。"""

from __future__ import annotations

import copy
import threading
import time
from typing import Any, Callable

from .storage import TrajectoryStore


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


class TrajectoryManager:
    """协调录制、持久化和回放，可注入硬件以进行离线测试。"""

    def __init__(self, store: TrajectoryStore | None = None, hardware: Any | None = None) -> None:
        self.store = store or TrajectoryStore()
        self._hardware = hardware
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._mode = "idle"
        self._started_at: float | None = None
        self._active_name: str | None = None
        self._active_id: str | None = None
        self._progress = 0.0
        self._last_error: str | None = None

    def _get_hardware(self) -> Any:
        if self._hardware is None:
            from .hardware import RosTrajectoryHardware
            self._hardware = RosTrajectoryHardware()
            self._hardware.wait_ready()
        return self._hardware

    def status(self) -> dict[str, Any]:
        with self._lock:
            elapsed = time.monotonic() - self._started_at if self._started_at else 0.0
            return {
                "mode": self._mode,
                "active_name": self._active_name,
                "active_id": self._active_id,
                "elapsed": round(elapsed, 2),
                "progress": round(self._progress, 3),
                "last_error": self._last_error,
            }

    def list_trajectories(self) -> list[dict[str, Any]]:
        return self.store.list()

    def start_record(self, *, name: str, sample_interval: float, max_duration: float) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ValueError("轨迹名称不能为空")
        interval = _clamp(sample_interval, 0.02, 1.0)
        duration = _clamp(max_duration, 1.0, 600.0)
        hardware = self._get_hardware()
        with self._lock:
            self._ensure_idle()
            self._mode = "recording"
            self._active_name = name[:80]
            self._active_id = None
            self._started_at = time.monotonic()
            self._progress = 0.0
            self._last_error = None
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._record_worker,
                args=(hardware, self._active_name, interval, duration),
                daemon=True,
                name="trajectory_record",
            )
            self._worker.start()
        return self.status()

    def stop_record(self) -> dict[str, Any]:
        with self._lock:
            if self._mode != "recording":
                raise RuntimeError("当前没有正在录制的轨迹")
        self._stop_and_join()
        return self.status()

    def start_replay(
        self,
        *,
        trajectory_id: str,
        speed_scale: float = 1.0,
        smoothing: float = 0.0,
        repeat_count: int = 1,
        safety_confirmed: bool = False,
    ) -> dict[str, Any]:
        if not safety_confirmed:
            raise ValueError("回放前必须确认机器人周围安全")
        speed = _clamp(speed_scale, 0.1, 2.0)
        smooth = _clamp(smoothing, 0.0, 0.95)
        repeats = max(1, min(10, int(repeat_count)))
        trajectory = self.store.get(trajectory_id)
        if not trajectory["frames"]:
            raise ValueError("轨迹没有可回放帧")
        hardware = self._get_hardware()
        with self._lock:
            self._ensure_idle()
            self._mode = "replaying"
            self._active_id = trajectory_id
            self._active_name = trajectory["name"]
            self._started_at = time.monotonic()
            self._progress = 0.0
            self._last_error = None
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._replay_worker,
                args=(hardware, trajectory, speed, smooth, repeats),
                daemon=True,
                name="trajectory_replay",
            )
            self._worker.start()
        return self.status()

    def stop_replay(self) -> dict[str, Any]:
        with self._lock:
            if self._mode != "replaying":
                raise RuntimeError("当前没有正在回放的轨迹")
        self._stop_and_join()
        return self.status()

    def stop_active(self) -> None:
        with self._lock:
            active = self._mode != "idle"
        if active:
            self._stop_and_join()

    def _ensure_idle(self) -> None:
        if self._mode != "idle":
            raise RuntimeError(f"机器人正处于 {self._mode} 状态，请先停止当前任务")

    def _stop_and_join(self) -> None:
        self._stop.set()
        worker = self._worker
        if worker and worker is not threading.current_thread():
            worker.join(timeout=5)

    def _record_worker(self, hardware: Any, name: str, interval: float, max_duration: float) -> None:
        frames: list[dict[str, Any]] = []
        started = time.monotonic()
        next_sample = started
        next_teach_heartbeat = started
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                elapsed = now - started
                if elapsed >= max_duration:
                    break
                if now >= next_teach_heartbeat:
                    hardware.set_teach_mode()
                    next_teach_heartbeat = now + 0.1
                if now >= next_sample:
                    frame = hardware.snapshot()
                    frame["t"] = round(elapsed, 6)
                    frames.append(frame)
                    with self._lock:
                        self._progress = min(1.0, elapsed / max_duration)
                    next_sample += interval
                wake_at = min(next_sample, next_teach_heartbeat, started + max_duration)
                self._stop.wait(max(0.0, wake_at - time.monotonic()))
            if frames:
                metadata = self.store.save(
                    name=name,
                    sample_interval=interval,
                    max_duration=max_duration,
                    duration=time.monotonic() - started,
                    frames=frames,
                )
                with self._lock:
                    self._active_id = metadata["id"]
            else:
                raise RuntimeError("未采集到任何轨迹帧")
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
        finally:
            self._finish()

    def _replay_worker(self, hardware: Any, trajectory: dict[str, Any], speed: float, smoothing: float, repeats: int) -> None:
        frames = trajectory["frames"]
        total = len(frames) * repeats
        interval = trajectory["sample_interval"] / speed
        previous: dict[str, Any] | None = None
        completed = 0
        try:
            for _ in range(repeats):
                for raw_frame in frames:
                    if self._stop.is_set():
                        return
                    frame = self._smooth_frame(previous, raw_frame, smoothing)
                    hardware.publish_frame(frame, speed)
                    previous = frame
                    completed += 1
                    with self._lock:
                        self._progress = completed / total
                    if self._stop.wait(interval):
                        return
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
        finally:
            self._finish()

    @staticmethod
    def _smooth_frame(previous: dict[str, Any] | None, current: dict[str, Any], amount: float) -> dict[str, Any]:
        if previous is None or amount <= 0:
            return copy.deepcopy(current)
        result = copy.deepcopy(current)
        for key, value in current["arms"].items():
            result["arms"][key] = previous["arms"][key] * amount + float(value) * (1 - amount)
        for hand_key in ("lhand", "rhand"):
            result[hand_key] = [
                old * amount + float(new) * (1 - amount)
                for old, new in zip(previous[hand_key], current[hand_key])
            ]
        return result

    def _finish(self) -> None:
        with self._lock:
            self._mode = "idle"
            self._started_at = None
            self._worker = None
            self._progress = 1.0 if self._last_error is None else self._progress
            self._stop.clear()


_manager: TrajectoryManager | None = None


def get_trajectory_manager() -> TrajectoryManager:
    global _manager
    if _manager is None:
        _manager = TrajectoryManager()
    return _manager
