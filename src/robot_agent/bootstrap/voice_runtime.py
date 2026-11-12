"""
voice_runtime.py - 可恢复的原有 ASR 与 LangGraph 语音流程
"""

from __future__ import annotations

import asyncio
import os
import uuid
from concurrent.futures import CancelledError, Future

from dotenv import load_dotenv

# 加载 .env 环境变量到 os.environ，以便 LangSmith 等依赖可直接读取
load_dotenv()

# ── ROS2 自定义消息包路径注入 ────────────────────────────────────
# 在未 source ros2ws 的情况下（如直接 python -m ...），
# 手动把 bodyctrl_msgs 的 Python 包路径加入 sys.path。
import sys as _sys
import glob as _glob

_ROS2_WS = os.environ.get(
    "ROS2_WS", "/opt/PARTITIONS/A/ros2ws"
)
# 匹配类似 install/*/local/lib/python*/dist-packages 的路径
_dist_pkgs = _glob.glob(
    f"{_ROS2_WS}/install/*/local/lib/python*/dist-packages"
)
for _p in _dist_pkgs:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
if _dist_pkgs:
    pass  # 路径已注入，bodyctrl_msgs 应可 import


from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.asr.sherpa_adapter import SherpaRecognizer
from src.robot_agent.capabilities.asr.text_cleaner import DuplicateFilter, is_valid_cjk_latin_text
from src.robot_agent.graph.nodes.wake_guard import is_exit_text, is_stop_text
from src.robot_agent.graph.state import AgentState
from src.robot_agent.interfaces.audio.device_resolver import (
    log_audio_device_selection,
    resolve_audio_devices,
)
from src.robot_agent.interfaces.audio.microphone import MicrophoneListener
from src.robot_agent.runtime import runtime_session
from src.robot_agent.settings import settings
from src.robot_agent.tools.registry import ToolRegistry

logger = get_logger(__name__)
SESSION_ID = "session_" + uuid.uuid4().hex[:8]


def _contains_chinese(text: str) -> bool:
    """Return True when text contains at least one CJK unified ideograph."""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)




def _configure_audio_devices() -> tuple[int | None, int | None]:
    """解析跨机器的音频设备并应用 sounddevice 默认值。"""
    try:
        import sounddevice as sd
    except ImportError:
        raise RuntimeError("sounddevice unavailable") from None

    allow_shared_device = os.getenv("AUDIO_ALLOW_SHARED_DEVICE", "1").lower() not in (
        "0",
        "false",
        "no",
        "n",
    )
    input_device, output_device = resolve_audio_devices(
        input_preference=settings.audio.input_device,
        output_preference=settings.audio.output_device,
        allow_shared_device=allow_shared_device,
    )
    if input_device is None or output_device is None:
        raise RuntimeError("app: no usable audio input/output device found")

    sd.default.device = (input_device, output_device)
    log_audio_device_selection(input_device=input_device, output_device=output_device)
    return input_device, output_device


async def handle_asr_result(asr_text: str, emotion: str = "neutral") -> None:
    """将 ASR 结果推送到图中，并持久化唤醒状态的更改。"""
    from src.robot_agent.graph.agent_graph import agent_graph

    async with runtime_session.graph_lock:
        current_wake_state = runtime_session.wake_state
        state = AgentState(
            session_id=SESSION_ID,
            user_id="default",
            input_text=asr_text,
            normalized_text=asr_text,
            language=settings.lang,
            emotion=emotion,
            wake_state=current_wake_state,
            interrupt_revision=runtime_session.get_interrupt_revision(),
        )

        logger.info(
            "app: invoking graph",
            input=asr_text[:80],
            emotion=emotion,
            wake_state=current_wake_state,
        )
        result = await agent_graph.ainvoke(state)

        current_revision = runtime_session.get_interrupt_revision()
        if state.interrupt_revision != current_revision:
            logger.info(
                "app: stale graph result skipped after interrupt",
                input=asr_text[:80],
                task_revision=state.interrupt_revision,
                current_revision=current_revision,
            )
            return

        next_wake_state = result.get("wake_state", current_wake_state)
        if next_wake_state != runtime_session.wake_state:
            logger.info(
                "app: wake state changed",
                previous=runtime_session.wake_state,
                current=next_wake_state,
            )
        runtime_session.wake_state = next_wake_state

        logger.info(
            "app: graph done",
            response=result.get("response_text", "")[:80],
            wake_state=runtime_session.wake_state,
        )



from src.robot_agent.bootstrap.audio_readiness import require_configured_audio, select_configured_audio, ComponentUnavailable
from src.robot_agent.bootstrap.voice_tasks import VoiceTasks, WelcomeOnce
from src.robot_agent.bootstrap.health import runtime_health

_asr = None
_welcome_once = WelcomeOnce()


def _prepare_voice():
    """Configure explicit audio and load the offline recognizer once, off the control loop."""
    global _asr
    require_configured_audio()
    select_configured_audio()
    _configure_audio_devices()
    ToolRegistry.get_instance()
    if _asr is None:
        recognizer = SherpaRecognizer()
        recognizer.initialize()
        _asr = recognizer
    return _asr


async def _welcome():
    """Preserve the welcome feature without making control or mic readiness wait on a cloud call."""
    if os.getenv('AGENT_PLAY_WELCOME', '1').lower() not in ('1', 'true', 'yes'):
        return
    try:
        from src.robot_agent.capabilities.tts.minimax_adapter import get_tts
        text = '你好，我是天轶机器人' if settings.lang == 'cn' else 'Hello, I am Tianyi robot'
        await _welcome_once.play(get_tts(), text, settings.lang, runtime_session)
    except Exception as exc:
        logger.warning('agent: welcome unavailable', error_type=type(exc).__name__)


async def run_voice(on_ready) -> None:
    """Run the original speech pipeline while supervising the actual input stream."""
    def cloud_ready():
        components = runtime_health.snapshot()['components']
        return all(components.get('cloud_' + name, {}).get('state') == 'ready'
                   for name in ('llm', 'tts'))
    speech_tasks = VoiceTasks(asyncio.get_running_loop(), can_submit=cloud_ready)
    mic = None
    welcome = None
    try:
        asr = await asyncio.to_thread(_prepare_voice)
        duplicate_filter = DuplicateFilter()
        loop = asyncio.get_running_loop()

        def interrupt_tts(reason: str, text: str) -> None:
            """尽可能快地中断当前语音。"""
            runtime_session.request_tts_interrupt()
            from src.robot_agent.interfaces.audio.playback_controller import get_playback_controller
            get_playback_controller().pause()

            cancelled_count = speech_tasks.cancel_current()

            logger.info(
                "app: tts interrupt requested",
                reason=reason,
                text=text[:80],
                cancelled_tasks=cancelled_count,
            )

        def on_segment(frames) -> None:
            """处理一个 VAD 音频分段。"""
            try:
                recognition = asr.recognize_with_metadata(frames)
            except Exception as exc:
                logger.exception("app: ASR failed", error=str(exc))
                return

            if not recognition:
                return

            cleaned_text = recognition.cleaned_text
            if not cleaned_text:
                return

            if not is_valid_cjk_latin_text(cleaned_text):
                logger.debug("app: non-CJK/Latin text filtered", text=cleaned_text[:80])
                return

            if (
                settings.lang == "cn"
                and settings.ignore_non_chinese_input
                and not _contains_chinese(cleaned_text)
            ):
                logger.info("app: non-Chinese ASR text ignored", text=cleaned_text[:80])
                return

            if runtime_session.wake_state == "awake" and is_stop_text(cleaned_text, settings.lang):
                interrupt_tts(reason="stop_word", text=cleaned_text)
                return

            if runtime_session.wake_state == "awake" and is_exit_text(cleaned_text, settings.lang):
                interrupt_tts(reason="exit_word", text=cleaned_text)

            if runtime_session.should_ignore_self_echo(cleaned_text):
                logger.info("app: probable self-echo ignored", text=cleaned_text[:80])
                return

            if duplicate_filter.is_duplicate(cleaned_text):
                logger.debug("app: duplicate ASR text skipped", text=cleaned_text)
                return

            # Keep long healthy replies intact; cancel on observed loss, never queue speech.
            future = speech_tasks.submit(lambda: handle_asr_result(
                cleaned_text, emotion=recognition.emotion))
            if future is None:
                return

            def cleanup(done_future: Future) -> None:
                try:
                    done_future.result()
                except CancelledError:
                    logger.debug("app: handle_asr_result cancelled")
                except Exception as exc:
                    logger.exception("app: handle_asr_result failed", error=str(exc))

            future.add_done_callback(cleanup)

        mic = MicrophoneListener(on_segment=on_segment)
        mic.start()
        for _ in range(50):
            if mic.is_ready():
                break
            if mic.has_failed():
                raise ComponentUnavailable('input_stream_failed')
            await asyncio.sleep(0.1)
        else:
            raise ComponentUnavailable('input_stream_not_ready')
        on_ready()
        welcome = asyncio.create_task(_welcome())
        while True:
            await asyncio.sleep(2)
            await asyncio.to_thread(require_configured_audio)
            if not cloud_ready():
                speech_tasks.cancel_current()
            if not mic.is_ready():
                raise ComponentUnavailable('input_stream_lost')
    finally:
        # Discard buffered speech on a lost device; never replay it after recovery.
        speech_tasks.close()
        if mic is not None:
            await asyncio.to_thread(mic.stop, flush=False)
        if welcome is not None:
            welcome.cancel()
            await asyncio.gather(welcome, return_exceptions=True)
        await speech_tasks.wait_closed()
