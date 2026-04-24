"""
capabilities/tts/kokoro_adapter.py - Kokoro WebSocket TTS 适配器

这个模块封装 Kokoro WebSocket TTS 调用，负责：
1. 向 Kokoro 服务发送文本、音色、语速和语言参数
2. 优先兼容本地 `ws_rvc_tts` 的双帧返回协议
3. 在双帧协议不可用时回退到通用流式 WebSocket 协议
4. 将返回的 PCM 音频直接播放，并响应打断事件

主要接口：
- `KokoroTTS.speak(text, lang="cn", interrupt_event=None)`：合成并播放一段文本
- `KokoroTTS.stop()`：停止当前播放
- `get_tts()`：返回进程级 TTS 单例，供图节点或应用层复用

用法：
    from src.robot_agent.capabilities.tts.kokoro_adapter import get_tts

    tts = get_tts()
    await tts.speak("你好，罗比特", lang="cn")
"""

from __future__ import annotations

import asyncio
import json
import ssl
import struct
import threading
from typing import Any

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.tts.base import TTSBase
from src.robot_agent.settings import settings

logger = get_logger(__name__)


class KokoroTTS(TTSBase):
    """Kokoro WebSocket TTS 适配器。"""

    def __init__(self) -> None:
        self._ws_url = settings.tts.ws_url
        self._default_voice = settings.tts.default_voice
        self._speed = settings.tts.speed
        self._output_device = settings.audio.output_device
        self._current_interrupt: threading.Event | None = None

    async def speak(
        self,
        text: str,
        lang: str = "cn",
        interrupt_event: threading.Event | None = None,
    ) -> None:
        """通过 WebSocket 请求 TTS 并播放返回音频。"""
        text = text.strip()
        if not text:
            return

        self._current_interrupt = interrupt_event or threading.Event()
        logger.info(
            "KokoroTTS: speaking",
            text=text[:80],
            lang=lang,
            ws_url=self._ws_url,
            voice=self._default_voice,
        )

        try:
            await self._speak_rvc_mode(text=text, lang=lang, interrupt_event=self._current_interrupt)
            return
        except Exception as exc:
            logger.warning("KokoroTTS: rvc mode failed, fallback to stream mode", error=str(exc))

        await self._speak_stream_mode(text=text, lang=lang, interrupt_event=self._current_interrupt)

    async def stop(self) -> None:
        """请求停止当前语音播放。"""
        if self._current_interrupt:
            self._current_interrupt.set()
            logger.info("KokoroTTS: stopped")

    async def _speak_rvc_mode(
        self,
        text: str,
        lang: str,
        interrupt_event: threading.Event,
    ) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("未安装 websockets") from exc

        ssl_context: ssl.SSLContext | None = None
        if self._ws_url.startswith("wss://"):
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        async with websockets.connect(self._ws_url, ssl=ssl_context) as websocket:
            payload = {
                "text": text,
                "voice": self._default_voice,
                "speed": self._speed,
                "lang": lang,
            }
            await websocket.send(json.dumps(payload))

            if interrupt_event.is_set():
                return

            header = await websocket.recv()
            pcm_data = await websocket.recv()

        if not isinstance(header, bytes):
            raise RuntimeError("KokoroTTS 收到的 header 不是 bytes")
        if not isinstance(pcm_data, bytes):
            raise RuntimeError("KokoroTTS 收到的 PCM 数据不是 bytes")
        if len(header) < 28:
            raise RuntimeError("KokoroTTS 收到的 WAV header 长度不足")

        channels = struct.unpack("<H", header[22:24])[0]
        sample_rate = struct.unpack("<I", header[24:28])[0]

        logger.info(
            "KokoroTTS: received rvc audio",
            sample_rate=sample_rate,
            channels=channels,
            pcm_bytes=len(pcm_data),
        )

        await asyncio.to_thread(
            self._play_pcm_blocking,
            pcm_data,
            sample_rate,
            channels,
            interrupt_event,
        )

    async def _speak_stream_mode(
        self,
        text: str,
        lang: str,
        interrupt_event: threading.Event,
    ) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("未安装 websockets") from exc

        ssl_context: ssl.SSLContext | None = None
        if self._ws_url.startswith("wss://"):
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        async with websockets.connect(self._ws_url, ssl=ssl_context) as websocket:
            start_msg = {
                "event": "task_start",
                "voice": self._default_voice,
                "lang": lang,
                "voice_setting": {
                    "voice": self._default_voice,
                    "voice_id": self._default_voice,
                    "speed": self._speed,
                    "vol": 1,
                    "pitch": 0,
                    "emotion": "neutral",
                },
                "audio_setting": {
                    "sample_rate": 24000,
                    "bitrate": 128000,
                    "format": "pcm",
                    "channel": 1,
                },
            }
            await websocket.send(json.dumps(start_msg))
            await websocket.send(json.dumps({"event": "task_continue", "text": text}))

            pcm_chunks: list[bytes] = []
            sample_rate = 24000
            channels = 1

            while True:
                if interrupt_event.is_set():
                    await websocket.send(json.dumps({"event": "task_finish"}))
                    return

                response_raw = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                response = json.loads(response_raw)

                if response.get("event") == "task_failed":
                    raise RuntimeError(f"流式 TTS 失败: {response}")

                data = response.get("data", {})
                if "sample_rate" in data:
                    sample_rate = int(data["sample_rate"])
                if "channel" in data:
                    channels = int(data["channel"])

                audio_hex = data.get("audio")
                if audio_hex:
                    pcm_chunks.append(bytes.fromhex(audio_hex))

                if response.get("is_final"):
                    break

            await websocket.send(json.dumps({"event": "task_finish"}))

        pcm_data = b"".join(pcm_chunks)
        if not pcm_data:
            raise RuntimeError("流式 TTS 未返回音频数据")

        logger.info(
            "KokoroTTS: received stream audio",
            sample_rate=sample_rate,
            channels=channels,
            pcm_bytes=len(pcm_data),
        )

        await asyncio.to_thread(
            self._play_pcm_blocking,
            pcm_data,
            sample_rate,
            channels,
            interrupt_event,
        )

    def _play_pcm_blocking(
        self,
        pcm_data: bytes,
        sample_rate: int,
        channels: int,
        interrupt_event: threading.Event,
    ) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("KokoroTTS 播放失败: 未安装 sounddevice") from exc

        dtype = "int16"
        chunk_bytes = max(sample_rate // 10, 1) * max(channels, 1) * 2

        stream_kwargs: dict[str, Any] = {
            "samplerate": sample_rate,
            "channels": channels,
            "dtype": dtype,
            "device": self._output_device or None,
        }

        with sd.RawOutputStream(**stream_kwargs) as stream:
            for offset in range(0, len(pcm_data), chunk_bytes):
                if interrupt_event.is_set():
                    break
                stream.write(pcm_data[offset : offset + chunk_bytes])


_tts_instance: KokoroTTS | None = None
_tts_lock = threading.Lock()


def get_tts() -> KokoroTTS:
    """返回进程级 TTS 单例。"""
    global _tts_instance

    if _tts_instance is not None:
        return _tts_instance

    with _tts_lock:
        if _tts_instance is None:
            _tts_instance = KokoroTTS()
        return _tts_instance
