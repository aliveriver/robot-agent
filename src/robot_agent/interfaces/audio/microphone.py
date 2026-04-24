"""
interfaces/audio/microphone.py - 麦克风采集与简单 VAD

封装基于 `sounddevice` 的本地麦克风采集，并用幅值阈值实现轻量级 VAD。
当检测到一段完整语音后，会将该段音频帧列表通过 `on_segment` 回调交给上层。

主要接口：
- `MicrophoneListener.start()`：启动麦克风采集和分段线程
- `MicrophoneListener.stop()`：停止采集并关闭输入流

用法：
    def on_segment(frames):
        ...

    mic = MicrophoneListener(on_segment=on_segment)
    mic.start()
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable, List, Optional

import numpy as np

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.settings import settings

logger = get_logger(__name__)

AudioSegmentCallback = Callable[[List[np.ndarray]], None]


class MicrophoneListener:
    """管理本地麦克风采集、缓存分段和简单 VAD。"""

    def __init__(
        self,
        on_segment: AudioSegmentCallback,
        input_device: Optional[int | str] = None,
    ) -> None:
        self._on_segment = on_segment
        self._input_device = input_device if input_device is not None else settings.audio.input_device
        self._audio_cfg = settings.audio

        self._stop_event = threading.Event()
        self._audio_frame_queue: queue.Queue[np.ndarray] = queue.Queue()

        self._current_audio_frames: list[np.ndarray] = []
        self._in_speech = False
        self._silence_started_at: float | None = None

        self._stream = None
        self._worker_thread: threading.Thread | None = None
        self._sample_rate = self._audio_cfg.sample_rate

    def start(self) -> None:
        """启动麦克风采集和语音分段线程。"""
        if self._worker_thread and self._worker_thread.is_alive():
            logger.warning("MicrophoneListener: already started")
            return

        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="microphone-listener",
        )
        self._worker_thread.start()
        logger.info(
            "MicrophoneListener: started",
            input_device=self._input_device,
            sample_rate=self._audio_cfg.sample_rate,
            blocksize=self._audio_cfg.blocksize,
            speech_threshold=self._audio_cfg.speech_threshold,
        )

    def stop(self) -> None:
        """停止麦克风采集并关闭输入流。"""
        self._stop_event.set()
        self._close_stream()

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)

        self._flush_current_segment(force=True)
        self._current_audio_frames = []
        self._in_speech = False
        self._silence_started_at = None
        self._clear_frame_queue()
        logger.info("MicrophoneListener: stopped")

    def _run(self) -> None:
        """启动输入流，并在后台线程中消费音频队列。"""
        self._start_input_stream()
        try:
            while not self._stop_event.is_set():
                try:
                    audio_chunk = self._audio_frame_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                try:
                    self._handle_audio_chunk(audio_chunk)
                finally:
                    self._audio_frame_queue.task_done()
        finally:
            self._close_stream()

    def _start_input_stream(self) -> None:
        """初始化并启动 `sounddevice` 输入流。"""
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("MicrophoneListener 启动失败: 未安装 sounddevice") from exc

        try:
            self._stream = sd.InputStream(
                samplerate=self._audio_cfg.sample_rate,
                channels=self._audio_cfg.channels,
                dtype="float32",
                blocksize=self._audio_cfg.blocksize,
                device=self._resolve_input_device(),
                callback=self._audio_input_callback,
            )
            self._stream.start()
        except Exception as exc:
            raise RuntimeError(f"MicrophoneListener 启动输入流失败: {exc}") from exc

    def _close_stream(self) -> None:
        """停止并关闭当前输入流。"""
        stream = self._stream
        if stream is None:
            return

        try:
            stream.stop()
        except Exception as exc:
            logger.warning("MicrophoneListener: stop stream failed", error=str(exc))

        try:
            stream.close()
        except Exception as exc:
            logger.warning("MicrophoneListener: close stream failed", error=str(exc))

        self._stream = None

    def _audio_input_callback(self, indata, frames, time_info, status) -> None:
        """接收输入流回调，并将音频块压入处理队列。"""
        if status:
            logger.warning("MicrophoneListener: input status", status=str(status))
        if self._stop_event.is_set() or indata is None or len(indata) == 0:
            return

        audio_chunk = np.squeeze(indata.copy())
        if audio_chunk.ndim == 0:
            audio_chunk = np.array([float(audio_chunk)], dtype=np.float32)
        if audio_chunk.ndim > 1:
            audio_chunk = audio_chunk.reshape(-1)

        if self._audio_cfg.input_gain != 1.0:
            audio_chunk = np.clip(audio_chunk * self._audio_cfg.input_gain, -1.0, 1.0)

        if self._audio_cfg.noise_floor > 0:
            audio_chunk = np.where(
                np.abs(audio_chunk) >= self._audio_cfg.noise_floor,
                audio_chunk,
                0.0,
            )

        self._audio_frame_queue.put(audio_chunk.astype(np.float32, copy=False))

    def _handle_audio_chunk(self, audio_chunk: np.ndarray) -> None:
        """根据阈值判断当前音频块是否属于语音段。"""
        peak = float(np.max(np.abs(audio_chunk))) if audio_chunk.size > 0 else 0.0
        now = time.time()

        if peak >= self._audio_cfg.speech_threshold:
            if not self._in_speech:
                self._in_speech = True
                self._current_audio_frames = []
                logger.info("MicrophoneListener: speech started")
            self._current_audio_frames.append(audio_chunk)
            self._silence_started_at = None
        elif self._in_speech:
            self._current_audio_frames.append(audio_chunk)
            if self._silence_started_at is None:
                self._silence_started_at = now

        if not self._in_speech:
            return

        buffered_duration = self._get_buffered_duration()
        silence_too_long = (
            self._silence_started_at is not None
            and (now - self._silence_started_at) >= self._audio_cfg.max_silence_sec
        )
        segment_too_long = buffered_duration >= self._audio_cfg.max_segment_sec

        if not silence_too_long and not segment_too_long:
            return

        self._flush_current_segment(force=False)

    def _get_buffered_duration(self) -> float:
        """计算当前缓存音频的时长。"""
        total_samples = sum(len(frame) for frame in self._current_audio_frames)
        return total_samples / float(self._sample_rate)

    def _flush_current_segment(self, force: bool) -> None:
        """输出当前缓存语音段，并重置内部状态。"""
        if not self._current_audio_frames:
            return

        frames = self._current_audio_frames
        duration = self._get_buffered_duration()

        self._current_audio_frames = []
        self._in_speech = False
        self._silence_started_at = None

        if duration < self._audio_cfg.min_segment_sec:
            logger.debug(
                "MicrophoneListener: segment dropped",
                duration=duration,
                forced=force,
            )
            return

        logger.info(
            "MicrophoneListener: segment ready",
            duration=duration,
            frame_count=len(frames),
            forced=force,
        )

        try:
            self._on_segment(frames)
        except Exception as exc:
            logger.exception("MicrophoneListener: on_segment failed", error=str(exc))

    def _resolve_input_device(self) -> int | str | None:
        """将配置中的输入设备转换为 sounddevice 可接受的类型。"""
        if self._input_device in (None, ""):
            return None

        if isinstance(self._input_device, str):
            device = self._input_device.strip()
            if not device:
                return None
            if device.isdigit():
                return int(device)
            return device

        return self._input_device

    def _clear_frame_queue(self) -> None:
        """清空待处理音频帧队列。"""
        with self._audio_frame_queue.mutex:
            self._audio_frame_queue.queue.clear()
