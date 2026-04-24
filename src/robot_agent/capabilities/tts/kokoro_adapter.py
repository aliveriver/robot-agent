"""
capabilities/tts/kokoro_adapter.py - Kokoro WebSocket TTS 适配器

负责通过 WebSocket 请求 Kokoro TTS，并将返回的 PCM 音频输出到本地设备。
这个适配器同时支持共享中断事件，用于尽快停止当前播报并清空设备缓冲：

1. 优先兼容 `ws_rvc_tts` 的双帧返回协议
2. 双帧协议失败时回退到通用流式协议
3. 播放阶段按短块检查 `interrupt_event`
4. 中断时主动 `abort()` 输出流，尽量减少残余播报

用法:
    from src.robot_agent.capabilities.tts.kokoro_adapter import get_tts

    tts = get_tts()
    await tts.speak("你好", lang="cn")
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
        """请求 TTS 并播放返回音频。"""
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
            await self._speak_rvc_mode(
                text=text,
                lang=lang,
                interrupt_event=self._current_interrupt,
            )
            return
        except Exception as exc:
            logger.warning("KokoroTTS: rvc mode failed, fallback to stream mode", error=str(exc))
        finally:
            if self._current_interrupt and self._current_interrupt.is_set():
                logger.info("KokoroTTS: speak interrupted")

        await self._speak_stream_mode(
            text=text,
            lang=lang,
            interrupt_event=self._current_interrupt,
        )

    async def stop(self) -> None:
        """停止当前播报。"""
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
            raise RuntimeError("缺少依赖 websockets") from exc

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
            if interrupt_event.is_set():
                return

            pcm_data = await websocket.recv()

        if interrupt_event.is_set():
            return
        if not isinstance(header, bytes):
            raise RuntimeError("KokoroTTS 返回的 header 不是 bytes")
        if not isinstance(pcm_data, bytes):
            raise RuntimeError("KokoroTTS 返回的 PCM 不是 bytes")
        if len(header) < 28:
            raise RuntimeError("KokoroTTS 返回的 WAV header 长度不合法")

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
            raise RuntimeError("缺少依赖 websockets") from exc

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
                    raise RuntimeError(f"TTS 任务失败: {response}")

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

        if interrupt_event.is_set():
            return

        pcm_data = b"".join(pcm_chunks)
        if not pcm_data:
            raise RuntimeError("TTS 没有返回可播放的 PCM 数据")

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
            raise RuntimeError("KokoroTTS 播放失败: 缺少依赖 sounddevice") from exc

        dtype = "int16"
        # 以 50ms 为一个检查块，降低从识别到停播的延迟。
        chunk_bytes = max(sample_rate // 20, 1) * max(channels, 1) * 2

        stream_kwargs: dict[str, Any] = {
            "samplerate": sample_rate,
            "channels": channels,
            "dtype": dtype,
            "device": self._output_device or None,
        }

        with sd.RawOutputStream(**stream_kwargs) as stream:
            for offset in range(0, len(pcm_data), chunk_bytes):
                if interrupt_event.is_set():
                    stream.abort()
                    logger.info("KokoroTTS: playback aborted")
                    return
                stream.write(pcm_data[offset : offset + chunk_bytes])


_tts_instance: KokoroTTS | None = None
_tts_lock = threading.Lock()


def get_tts() -> KokoroTTS:
    """获取进程级单例 TTS 实例。"""
    global _tts_instance

    if _tts_instance is not None:
        return _tts_instance

    with _tts_lock:
        if _tts_instance is None:
            _tts_instance = KokoroTTS()
        return _tts_instance
