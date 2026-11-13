"""猜拳时序：准备、回放/口令、出拳、判定、缓慢回到固定启动姿态。

run_game_round() 复用轨迹播放器和 AudioPlaybackController；GameTiming.from_env()
读取停顿和音频时序，CountdownAudio 根据音频实际长度安排开始时机。
"""

from __future__ import annotations

import math
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from script.game_audio import resolve_game_audio
from script.game_trajectory import replay_game_trajectory
from script.game_return import return_to_initial_pose


@dataclass(frozen=True)
class GameTiming:
    """保留准备、出手及观察停顿；支持在 .env 中根据实机效果调整。"""

    ready_sec: float = 3.0
    reveal_sec: float = 0.0
    observe_sec: float = 2.0
    countdown_sec: float = 1.5
    audio_lead_sec: float = 0.0

    @classmethod
    def from_env(cls) -> GameTiming:
        """读取秒数，拒绝无穷大、负值等无法执行的等待配置。"""
        fields = {
            "ready_sec": "GAME_READY_DELAY_SEC",
            "reveal_sec": "GAME_REVEAL_DELAY_SEC",
            "observe_sec": "GAME_RESULT_WAIT_SEC",
            "countdown_sec": "GAME_COUNTDOWN_DURATION_SEC",
            "audio_lead_sec": "GAME_AUDIO_LEAD_SEC",
        }
        defaults = cls()
        values = {name: float(os.getenv(env, str(getattr(defaults, name))))
                  for name, env in fields.items()}
        if any(not math.isfinite(value) or value < 0 for value in values.values()):
            raise ValueError("猜拳等待秒数必须为有限的非负数")
        return cls(**values)


class CountdownAudio:
    """按剩余轨迹时长启动本地音频；未配置文件时保留模拟口令的时间。"""

    def __init__(self, audio_path: Path | None, timing: GameTiming, source_type: str = "game_countdown"):
        self.source_type = source_type
        self.duration = timing.countdown_sec
        self.lead = timing.audio_lead_sec
        self.path = audio_path
        self.controller = None
        self.session_id = None
        self.started = False
        self.deadline = 0.0
        if audio_path is not None:
            import soundfile as sf
            from src.robot_agent.interfaces.audio.playback_controller import AudioPlaybackController

            info = sf.info(audio_path)
            if info.frames <= 0 or info.samplerate <= 0:
                raise ValueError(f"倒计时音频为空或采样率无效: {audio_path}")
            self.duration = info.frames / info.samplerate
            self.controller = AudioPlaybackController(output_device=os.getenv("AUDIO_OUTPUT_DEVICE") or None)

    def on_progress(self, elapsed: float, total: float) -> None:
        """轨迹剩余长度足以容纳口令时开始播放，长音频从第一帧前开始。"""
        if not self.started and total - elapsed <= self.duration + self.lead:
            self.start()
        if self.started:
            self.finished()

    def start(self) -> None:
        """启动一次异步播放，不阻塞轨迹逐帧发布。"""
        if self.started:
            return
        if self.controller is not None:
            status = self.controller.play(self.path, self.source_type)
            self.session_id = status["session_id"]
            print(f"[LOG][GAME] 播放猜拳音频 ({self.duration:.2f} 秒): {self.path}")
        else:
            print(f"[LOG][GAME] 尚未配置音频，模拟口令时序 {self.duration:.2f} 秒。")
        self.started = True
        now = time.monotonic()
        # 文件播放留出设备打开、缓冲排空的余量；实际结束以播放器状态为准。
        self.deadline = now + self.duration + (5.0 if self.controller else 0.0)

    def finished(self) -> bool:
        """查询音频是否已经播完，设备错误、中断和超时均取消本局。"""
        if not self.started:
            return False
        if self.controller is None:
            return time.monotonic() >= self.deadline
        status = self.controller.status()
        if status.get("last_error"):
            raise RuntimeError(f"口令音频播放失败: {status['last_error']}")
        if status["state"] == "idle":
            if status.get("last_completed_session_id") != self.session_id:
                raise RuntimeError("口令音频被中断，取消本局")
            return True
        if status["session_id"] != self.session_id:
            raise RuntimeError("口令音频被其他播放替换，取消本局")
        if time.monotonic() > self.deadline:
            raise TimeoutError("口令音频播放超时")
        return False

    def stop(self) -> None:
        """取消时停止本局音频，保留其他来源的播放会话。"""
        if self.controller is not None and self.started:
            if self.controller.status().get("session_id") == self.session_id:
                self.controller.stop()


def wait_with_ros(node, seconds: float) -> None:
    """保留 sleep 对应的停顿，同时刷新 ROS，持续保持手臂和手指目标。"""
    import rclpy

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not rclpy.ok():
            raise RuntimeError("ROS2 已退出，猜拳中断")
        rclpy.spin_once(node, timeout_sec=min(0.01, max(0.0, deadline - time.monotonic())))


def judge_while_holding(node, judge_result, robot_gesture: str) -> dict:
    """截图/API 在工作线程执行，主线程持续刷新 ROS，保持末帧和出拳手势。"""
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="game_judge")
    try:
        future = executor.submit(judge_result, robot_gesture)
        while not future.done():
            wait_with_ros(node, 0.01)
        return future.result()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)



def play_result_audio(node, root: Path, result: str, timing: GameTiming) -> None:
    """Play the human-perspective outcome while continuing ROS control callbacks."""
    audio = CountdownAudio(resolve_game_audio(root, result), timing, source_type="game_result")
    try:
        audio.start()
        while not audio.finished():
            wait_with_ros(node, 0.01)
    finally:
        audio.stop()


def run_game_round(node, *, gestures: dict, judge_result, db_path: Path,
                   trajectory_id: int, root: Path) -> dict:
    """回放并判定；正常结束、未知结果或中断后均回到固定启动姿态。"""
    timing = GameTiming.from_env()
    saved_pose = getattr(node, "game_initial_pose", None)
    # The game entrypoint captures this once, before any arm motion.
    # Standalone callers without a startup anchor retain measured-pose fallback.
    initial_pose = saved_pose.copy() if isinstance(saved_pose, dict) else node.capture_arm_pose()
    node.set_arm_mode('b', 'lock')
    configured_path = os.getenv("GAME_COUNTDOWN_AUDIO", "").strip()
    audio_path = Path(configured_path).expanduser() if configured_path else None
    if audio_path is not None and not audio_path.is_absolute():
        audio_path = root / audio_path
    if audio_path is None:
        audio_path = resolve_game_audio(root, "countdown")
    countdown = CountdownAudio(audio_path, timing)
    try:
        # 准备期用统一握拳姿势，最终随机结果在口令结束后才产生。
        node.set_hand_target('b', gestures['rock'])
        node.ctrl_cb()
        print(f"[LOG][GAME] 准备猜拳，停顿 {timing.ready_sec:.2f} 秒。")
        wait_with_ros(node, timing.ready_sec)
        replay_game_trajectory(node, db_path, trajectory_id, on_progress=countdown.on_progress)
        countdown.start()
        while not countdown.finished():
            wait_with_ros(node, 0.01)
        wait_with_ros(node, timing.reveal_sec)

        action_name, action_values = random.choice(list(gestures.items()))
        node.set_hand_target('b', action_values)
        node.ctrl_cb()
        print(f"[LOG][GAME] 最终出手: {action_name.upper()}，等待亮拳 {timing.observe_sec:.2f} 秒。")
        wait_with_ros(node, timing.observe_sec)
        print("[LOG][GAME] 保持出拳姿势，等待视觉判定完成。")
        judgment = judge_while_holding(node, judge_result, action_name)
        labels = {"win": "你赢了", "lose": "机器人赢了", "draw": "平局", "unknown": "无法判定"}
        print(f"[LOG][GAME] 玩家: {judgment['player_gesture']}，结果: {labels[judgment['result']]}")
        if judgment['result'] in ('win', 'lose'):
            try:
                play_result_audio(node, root, judgment['result'], timing)
            except Exception as exc:
                # A speaker failure must not prevent the normal arm return.
                print(f"[LOG][GAME] 胜负语音播放失败: {exc}")
        return judgment
    finally:
        try:
            countdown.stop()
        finally:
            # Never leave the robot holding a raised gesture after an API failure.
            print("[LOG][GAME] 本局结束，松开双手并缓慢回到固定初始姿态。")
            node.set_hand_target('b', gestures['paper'])
            node.ctrl_cb()
            return_to_initial_pose(node, initial_pose)
