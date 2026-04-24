"""
runtime.py - 进程级运行态

集中保存同一次进程启动内共享的状态与同步原语，避免各模块各自维护：

1. `wake_state`：机器人当前唤醒状态
2. `graph_lock`：串行化图执行，避免多轮状态竞争
3. `tts_interrupt_event`：跨 ASR / Graph / TTS 的共享中断信号

用法:
    from src.robot_agent.runtime import runtime_session

    runtime_session.request_tts_interrupt()
    runtime_session.clear_tts_interrupt()
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field

from src.robot_agent.capabilities.asr.text_cleaner import is_self_echo


@dataclass
class RuntimeSession:
    """保存进程内持续状态与共享中断信号。"""

    wake_state: str = "sleep"
    graph_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tts_interrupt_event: threading.Event = field(default_factory=threading.Event)
    interrupt_revision: int = 0
    state_lock: threading.Lock = field(default_factory=threading.Lock)
    duplicate_text_window: float = 2.0
    self_echo_window: float = 6.0
    last_spoken_text: str = ""
    last_spoken_at: float = 0.0
    last_spoken_finished_at: float = 0.0

    def request_tts_interrupt(self) -> int:
        """设置 TTS 中断信号，要求当前播报尽快停止。"""
        with self.state_lock:
            self.interrupt_revision += 1
            self.tts_interrupt_event.set()
            return self.interrupt_revision

    def clear_tts_interrupt(self) -> None:
        """清空旧的中断信号，允许下一次正常播报开始。"""
        self.tts_interrupt_event.clear()

    def get_interrupt_revision(self) -> int:
        """获取当前中断版本号，用于标记任务是否已过期。"""
        with self.state_lock:
            return self.interrupt_revision

    def allow_tts_playback(self, expected_revision: int) -> bool:
        """
        判断当前播报任务是否仍然有效。

        只有当任务创建时看到的版本号仍然是最新版本，才允许清空中断信号并开始播报。
        """
        with self.state_lock:
            if expected_revision != self.interrupt_revision:
                return False
            self.tts_interrupt_event.clear()
            return True

    def should_ignore_self_echo(self, asr_text: str) -> bool:
        """判断当前 ASR 文本是否大概率是机器人刚刚播报过的自回声。"""
        with self.state_lock:
            last_spoken_text = self.last_spoken_text
            last_tts_time = max(self.last_spoken_at, self.last_spoken_finished_at)
            self_echo_window = self.self_echo_window

        if not last_spoken_text:
            return False

        if (time.time() - last_tts_time) >= self_echo_window:
            return False

        return is_self_echo(asr_text, last_spoken_text)

    def should_skip_duplicate_tts(self, text: str) -> bool:
        """判断是否应跳过短时间内的重复播报。"""
        with self.state_lock:
            if not self.last_spoken_text:
                return False
            return text == self.last_spoken_text and (
                time.time() - self.last_spoken_at
            ) < self.duplicate_text_window

    def note_spoken_start(self, text: str) -> None:
        """记录一段 TTS 文本开始播报。"""
        with self.state_lock:
            self.last_spoken_text = text
            self.last_spoken_at = time.time()

    def note_spoken_finish(self) -> None:
        """记录最近一次 TTS 播报完成或结束的时间。"""
        with self.state_lock:
            self.last_spoken_finished_at = time.time()


runtime_session = RuntimeSession()
