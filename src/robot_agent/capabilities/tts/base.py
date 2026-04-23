"""
capabilities/tts/base.py — TTS 抽象接口

定义 TTS（语音合成）能力的标准协议。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import threading


class TTSBase(ABC):
    """TTS 能力基类，所有 TTS 适配器都继承此类"""

    @abstractmethod
    async def speak(
        self,
        text: str,
        lang: str = "cn",
        interrupt_event: threading.Event | None = None,
    ) -> None:
        """
        将文本转换为语音并播放（异步）。

        Args:
            text:            要播放的文本
            lang:            语言代码（cn / en）
            interrupt_event: 设置此 Event 可中途打断播放
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """立即停止当前正在播放的语音"""
        ...
