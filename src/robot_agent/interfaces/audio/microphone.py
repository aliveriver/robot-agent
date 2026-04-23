"""
interfaces/audio/microphone.py — 麦克风输入接口

负责：
- 音频设备选择与初始化
- 持续录音 + 语音分段（VAD）
- 将音频段送入回调或队列

这一层只产出音频帧，不做任何 ASR 或业务逻辑。

TODO: 将 tianyi_v1.py 中 sounddevice 音频采集逻辑迁移至此。
"""

from __future__ import annotations

import queue
import threading
from typing import Callable, List, Optional

import numpy as np

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.settings import settings

logger = get_logger(__name__)

# 音频段回调类型：接收 numpy 帧列表
AudioSegmentCallback = Callable[[List[np.ndarray]], None]


class MicrophoneListener:
    """
    麦克风监听器。

    使用 VAD（基于能量阈值）对连续音频流进行分段，
    每段语音结束后触发 on_segment 回调。
    """

    def __init__(
        self,
        on_segment: AudioSegmentCallback,
        input_device: Optional[int] = None,
    ) -> None:
        self._on_segment = on_segment
        self._input_device = input_device
        self._stop_event = threading.Event()
        self._audio_cfg = settings.audio

    def start(self) -> None:
        """在后台线程启动麦克风监听"""
        t = threading.Thread(target=self._run, daemon=True, name="microphone-listener")
        t.start()
        logger.info("MicrophoneListener: started")

    def stop(self) -> None:
        """停止监听"""
        self._stop_event.set()
        logger.info("MicrophoneListener: stopped")

    def _run(self) -> None:
        """
        后台线程主循环。

        TODO: 使用 sounddevice.InputStream 采集音频
        TODO: 迁移 tianyi_v1.py 中 _audio_callback + VAD 分段逻辑
        """
        logger.info("MicrophoneListener: _run() is a stub, needs implementation")
        # import sounddevice as sd
        # with sd.InputStream(
        #     samplerate=self._audio_cfg.sample_rate,
        #     channels=self._audio_cfg.channels,
        #     blocksize=self._audio_cfg.blocksize,
        #     dtype='float32',
        #     device=self._input_device,
        #     callback=self._audio_callback,
        # ):
        #     self._stop_event.wait()
