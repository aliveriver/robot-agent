"""
capabilities/tts/minimax_adapter.py - Minimax WebSocket TTS 适配器

负责通过 Minimax WebSocket TTS 合成并播放语音，兼容旧版 `tianyi_v1.py`
的流式协议，同时保留当前项目的共享中断语义：

1. `speak(...)` 建立 WebSocket 连接并流式接收 PCM 音频
2. `_StreamAudioPlayer` 优先使用 `sounddevice`，失败时回退到 `mpv/ffplay`
3. 播放和接收循环持续检查 `interrupt_event`，尽快中断当前播报
4. `get_tts()` 提供进程级单例，供图节点直接调用

用法:
    from src.robot_agent.capabilities.tts.minimax_adapter import get_tts

    tts = get_tts()
    await tts.speak("你好", lang="cn")
"""

from __future__ import annotations

import asyncio
import json
import shutil
import ssl
import subprocess
import threading
from typing import Any

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.tts.base import TTSBase
from src.robot_agent.settings import settings

logger = get_logger(__name__)

DEFAULT_VOICE_BY_LANG = {
    "cn": "female-shaonv",
    "en": "English_Trustworthy_Man",
}


class _StreamAudioPlayer:
    """流式音频播放器。"""

    def __init__(self, output_device: str | int | None = None) -> None:
        self._output_device = output_device
        self._stream: Any | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._command_name: str | None = None
        self.stopped = False

    def start(self) -> bool:
        """初始化播放器。"""
        if self._start_sounddevice():
            return True
        return self._start_subprocess_player()

    def write(self, audio_hex: str) -> bool:
        """写入一段 PCM 十六进制音频。"""
        if not audio_hex:
            return False

        try:
            audio_bytes = bytes.fromhex(audio_hex)
            if self._stream is not None:
                self._stream.write(audio_bytes)
                return True

            if self._process is None or self._process.stdin is None:
                return False

            self._process.stdin.write(audio_bytes)
            self._process.stdin.flush()
            return True
        except BrokenPipeError:
            logger.warning("MinimaxTTS: player pipe closed while writing audio")
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("MinimaxTTS: failed to write audio chunk", error=str(exc))
            return False

    def stop(self) -> None:
        """立即中断当前播放。"""
        self.stopped = True

        if self._stream is not None:
            try:
                self._stream.abort()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        if self._process is not None and self._process.stdin is not None:
            try:
                self._process.stdin.close()
            except Exception:
                pass

        if self._process is not None and self._process.poll() is None:
            try:
                self._process.terminate()
            except Exception:
                pass
            try:
                self._process.wait(timeout=1)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass

    def finish(self) -> None:
        """正常结束播放并回收资源。"""
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        if self._process is not None and self._process.stdin is not None:
            try:
                self._process.stdin.close()
            except Exception:
                pass

        if self._process is not None:
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                try:
                    self._process.terminate()
                except Exception:
                    pass
            except Exception:
                pass

    def _start_sounddevice(self) -> bool:
        try:
            import sounddevice as sd
        except ImportError:
            return False

        try:
            self._stream = sd.RawOutputStream(
                samplerate=32000,
                channels=1,
                dtype="int16",
                blocksize=1024,
                latency="low",
                device=self._output_device or None,
            )
            self._stream.start()
            self._command_name = "sounddevice"
            logger.info(
                "MinimaxTTS: sounddevice output ready",
                output_device=self._output_device,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self._stream = None
            logger.warning("MinimaxTTS: failed to start sounddevice output", error=str(exc))
            return False

    def _start_subprocess_player(self) -> bool:
        candidates = []
        if shutil.which("mpv"):
            candidates.append(
                [
                    "mpv",
                    "--no-cache",
                    "--no-terminal",
                    "--demuxer=rawaudio",
                    "--demuxer-rawaudio-format=s16le",
                    "--demuxer-rawaudio-rate=32000",
                    "--demuxer-rawaudio-channels=1",
                    "--",
                    "fd://0",
                ]
            )
        if shutil.which("ffplay"):
            candidates.append(
                [
                    "ffplay",
                    "-nodisp",
                    "-autoexit",
                    "-loglevel",
                    "quiet",
                    "-fflags",
                    "nobuffer",
                    "-flags",
                    "low_delay",
                    "-f",
                    "s16le",
                    "-ar",
                    "32000",
                    "-ac",
                    "1",
                    "-i",
                    "pipe:0",
                ]
            )

        for command in candidates:
            try:
                self._process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._command_name = command[0]
                logger.info("MinimaxTTS: subprocess player ready", command=self._command_name)
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "MinimaxTTS: failed to start subprocess player",
                    command=command[0],
                    error=str(exc),
                )

        logger.error("MinimaxTTS: no supported audio player available")
        return False


class MinimaxTTS(TTSBase):
    """Minimax WebSocket TTS 适配器。"""

    def __init__(self) -> None:
        self._api_url = settings.tts.api_url
        self._ws_url = settings.tts.ws_url
        self._api_key = settings.tts.api_key.strip()
        self._model = settings.tts.model
        self._default_voice = settings.tts.default_voice.strip()
        self._speed = settings.tts.speed
        self._output_device = settings.audio.output_device
        self._current_interrupt: threading.Event | None = None
        self._current_player: _StreamAudioPlayer | None = None

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

        if not self._api_key:
            raise RuntimeError("MinimaxTTS 缺少 TTS_API_KEY 配置")

        self._current_interrupt = interrupt_event or threading.Event()
        logger.info(
            "MinimaxTTS: speaking",
            text=text[:80],
            lang=lang,
            model=self._model,
            ws_url=self._ws_url,
            api_url=self._api_url,
            voice=self._resolve_voice(lang),
        )

        try:
            await self._stream_tts(text=text, lang=lang, interrupt_event=self._current_interrupt)
        finally:
            if self._current_player is not None and not self._current_player.stopped:
                self._current_player.finish()
            self._current_player = None
            if self._current_interrupt and self._current_interrupt.is_set():
                logger.info("MinimaxTTS: speak interrupted")

    async def stop(self) -> None:
        """停止当前播报。"""
        if self._current_interrupt is not None:
            self._current_interrupt.set()
        if self._current_player is not None:
            self._current_player.stop()
        logger.info("MinimaxTTS: stopped")

    async def _stream_tts(
        self,
        text: str,
        lang: str,
        interrupt_event: threading.Event,
    ) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("MinimaxTTS 缺少依赖 websockets") from exc

        player = _StreamAudioPlayer(output_device=self._output_device)
        if not player.start():
            raise RuntimeError("MinimaxTTS 无法初始化音频播放器")
        self._current_player = player

        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        websocket = None
        try:
            websocket = await websockets.connect(
                self._ws_url,
                additional_headers={"Authorization": self._format_auth_header()},
                ssl=ssl_context,
            )

            connected = json.loads(await websocket.recv())
            if connected.get("event") != "connected_success":
                raise RuntimeError(f"MinimaxTTS 握手失败: {connected}")

            start_msg = {
                "event": "task_start",
                "model": self._model,
                "voice_setting": {
                    "voice_id": self._resolve_voice(lang),
                    "speed": self._speed,
                    "vol": 1,
                    "pitch": 0,
                    "emotion": "neutral",
                },
                "audio_setting": {
                    "sample_rate": 32000,
                    "bitrate": 128000,
                    "format": "pcm",
                    "channel": 1,
                },
            }
            await websocket.send(json.dumps(start_msg))

            started = json.loads(await websocket.recv())
            if started.get("event") != "task_started":
                raise RuntimeError(f"MinimaxTTS 任务启动失败: {started}")

            await websocket.send(json.dumps({"event": "task_continue", "text": text}))

            chunk_count = 0
            total_bytes = 0
            while True:
                if interrupt_event.is_set():
                    await self._finish_task(websocket)
                    player.stop()
                    return

                try:
                    response_raw = await asyncio.wait_for(websocket.recv(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue

                response = json.loads(response_raw)
                if response.get("event") == "task_failed":
                    raise RuntimeError(f"MinimaxTTS 任务失败: {response}")

                audio_hex = response.get("data", {}).get("audio")
                if audio_hex:
                    chunk_count += 1
                    total_bytes += len(audio_hex) // 2
                    if not player.write(audio_hex):
                        raise RuntimeError("MinimaxTTS 音频播放失败")

                if response.get("is_final"):
                    logger.info(
                        "MinimaxTTS: synthesis complete",
                        chunks=chunk_count,
                        audio_bytes=total_bytes,
                    )
                    break

            await self._finish_task(websocket)
        finally:
            if websocket is not None:
                try:
                    await self._finish_task(websocket)
                except Exception:
                    pass
                try:
                    await websocket.close()
                except Exception:
                    pass

    async def _finish_task(self, websocket: Any) -> None:
        """结束当前 WebSocket 合成任务。"""
        await websocket.send(json.dumps({"event": "task_finish"}))

    def _format_auth_header(self) -> str:
        """规范化 Authorization 头。"""
        if self._api_key.lower().startswith("bearer "):
            return self._api_key
        return f"Bearer {self._api_key}"

    def _resolve_voice(self, lang: str) -> str:
        """根据语言选择默认音色。"""
        if self._default_voice:
            return self._default_voice
        return DEFAULT_VOICE_BY_LANG.get(lang, DEFAULT_VOICE_BY_LANG["cn"])


_tts_instance: MinimaxTTS | None = None
_tts_lock = threading.Lock()


def get_tts() -> MinimaxTTS:
    """获取进程级单例 TTS 实例。"""
    global _tts_instance

    if _tts_instance is not None:
        return _tts_instance

    with _tts_lock:
        if _tts_instance is None:
            _tts_instance = MinimaxTTS()
        return _tts_instance
