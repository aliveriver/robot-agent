"""
capabilities/asr/base.py — ASR 抽象接口

定义 ASR（语音识别）能力的标准协议。
所有 ASR 实现（Sherpa、Qwen 等）都应遵循此接口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ASRBase(ABC):
    """ASR 能力基类，所有 ASR 适配器都继承此类"""

    @abstractmethod
    def recognize(self, audio_frames: list) -> str:
        """
        同步识别：将音频帧转为文本。

        Args:
            audio_frames: 音频帧列表（numpy array 列表）

        Returns:
            识别出的文本字符串，识别失败返回空字符串
        """
        ...

    @abstractmethod
    def get_language(self) -> str:
        """返回当前 ASR 识别的语言代码（cn / en）"""
        ...
