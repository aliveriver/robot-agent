"""
capabilities/tts/kokoro_adapter.py — Kokoro WebSocket TTS 适配器

将 tianyi_v1.py 中的 stream_synthesize_and_play（Kokoro 版本）
封装为符合 TTSBase 接口的独立模块。

TODO: 将原 WebSocket TTS 逻辑迁移至此。
"""

from __future__ import annotations

import threading

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.tts.base import TTSBase
from src.robot_agent.settings import settings

logger = get_logger(__name__)


class KokoroTTS(TTSBase):
    """
    Kokoro WebSocket TTS 适配器。

    通过 WebSocket 连接 Kokoro / RVC TTS 服务，流式合成并播放。
    """

    def __init__(self) -> None:
        self._ws_url = settings.tts.ws_url
        self._default_voice = settings.tts.default_voice
        self._speed = settings.tts.speed
        self._current_interrupt: threading.Event | None = None

    async def speak(
        self,
        text: str,
        lang: str = "cn",
        interrupt_event: threading.Event | None = None,
    ) -> None:
        """
        通过 Kokoro WebSocket 合成并播放语音。

        TODO: 将 tianyi_v1.py stream_synthesize_and_play（Kokoro 版）迁移至此
        """
        if not text or not text.strip():
            return

        self._current_interrupt = interrupt_event
        logger.info("KokoroTTS: speaking", text=text[:40], lang=lang)

        # TODO: 实现 WebSocket 连接 + 流式播放逻辑
        # 参考 tianyi_v1.py 中 KOKORO_WS_URL 相关代码

    async def stop(self) -> None:
        """通过设置 interrupt_event 停止当前播放"""
        if self._current_interrupt:
            self._current_interrupt.set()
            logger.info("KokoroTTS: stopped")
