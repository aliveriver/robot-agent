"""
app.py - 应用程序入口点
"""

from __future__ import annotations

import asyncio
import os
import subprocess
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


from src.robot_agent.bootstrap.camera_node import CameraNodeLauncher
from src.robot_agent.bootstrap.logging import get_logger, setup_logging
from src.robot_agent.capabilities.asr.sherpa_adapter import SherpaRecognizer
from src.robot_agent.capabilities.asr.text_cleaner import DuplicateFilter, is_valid_cjk_latin_text
from src.robot_agent.graph.agent_graph import agent_graph
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


def _configure_pulse_audio() -> None:
    """如果 pactl 可用，在 Linux 上配置 PulseAudio 默认设置。"""
    if os.name != "posix":
        return

    if os.getenv("DISABLE_PULSE_DEVICE_SETUP", "").lower() in ("1", "true", "yes", "y"):
        logger.info("app: PulseAudio device setup skipped (DISABLE_PULSE_DEVICE_SETUP)")
        return

    default_source = os.getenv(
        "PULSE_DEFAULT_SOURCE",
        "bluez_source.0C_9A_E6_F5_7B_14.handsfree_head_unit",
    )
    default_sink = os.getenv(
        "PULSE_DEFAULT_SINK",
        "alsa_output.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.analog-stereo",
    )

    def _run_pactl(args: list[str], label: str) -> None:
        try:
            result = subprocess.run(
                ["pactl", *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                logger.info(f"app: {label} ok", args=args)
            else:
                err = (result.stderr or result.stdout or "").strip()
                logger.warning(f"app: {label} failed", args=args, error=err)
        except FileNotFoundError:
            logger.debug("app: pactl not found, skipping PulseAudio setup")
        except Exception as exc:
            logger.warning(f"app: {label} error", error=str(exc))

    if default_source:
        _run_pactl(["set-default-source", default_source], "set default source")
    if default_sink:
        _run_pactl(["set-default-sink", default_sink], "set default sink")


def _configure_audio_devices() -> tuple[int | None, int | None]:
    """解析跨机器的音频设备并应用 sounddevice 默认值。"""
    try:
        import sounddevice as sd
    except ImportError:
        logger.warning("app: sounddevice not installed, skipping audio device configuration")
        return None, None

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


async def main() -> None:
    """启动机器人运行时并持续监听麦克风分段。"""
    setup_logging()
    logger.info("robot-agent: starting", lang=settings.lang, env=settings.env)

    # ── LangSmith tracing 状态检测 ──────────────────────────────
    _ls_tracing = os.getenv("LANGSMITH_TRACING", "").lower() in ("true", "1", "yes")
    _ls_project = os.getenv("LANGSMITH_PROJECT", "(未设置)")
    if _ls_tracing and os.getenv("LANGSMITH_API_KEY"):
        logger.info(
            "robot-agent: LangSmith tracing 已开启",
            project=_ls_project,
            endpoint=os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"),
        )
    else:
        logger.info(
            "robot-agent: LangSmith tracing 未开启"
            "（在 .env 中设置 LANGSMITH_TRACING=true 和 LANGSMITH_API_KEY 可开启）"
        )

    ToolRegistry.get_instance()
    camera_launcher = CameraNodeLauncher()
    pending_tasks: set[Future] = set()
    mic: MicrophoneListener | None = None

    try:
        _configure_pulse_audio()
        _configure_audio_devices()
        camera_launcher.start()

        asr = SherpaRecognizer()
        asr.initialize()

        welcome_text = (
            "你好，我是天轶机器人" if settings.lang == "cn" else "Hello, I am Tianyi robot"
        )
        try:
            from src.robot_agent.capabilities.tts.minimax_adapter import get_tts

            welcome_tts = get_tts()
            await welcome_tts.speak(welcome_text, lang=settings.lang)
            logger.info("app: welcome message played")
        except Exception as exc:
            logger.warning("app: welcome TTS failed, continuing startup", error=str(exc))

        duplicate_filter = DuplicateFilter()
        loop = asyncio.get_running_loop()

        def interrupt_tts(reason: str, text: str) -> None:
            """尽可能快地中断当前语音。"""
            runtime_session.request_tts_interrupt()

            cancelled_count = 0
            for future in list(pending_tasks):
                if future.cancel():
                    cancelled_count += 1

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

            future = asyncio.run_coroutine_threadsafe(
                handle_asr_result(cleaned_text, emotion=recognition.emotion),
                loop,
            )
            pending_tasks.add(future)

            def cleanup(done_future: Future) -> None:
                pending_tasks.discard(done_future)
                try:
                    done_future.result()
                except CancelledError:
                    logger.debug("app: handle_asr_result cancelled")
                except Exception as exc:
                    logger.exception("app: handle_asr_result failed", error=str(exc))

            future.add_done_callback(cleanup)

        mic = MicrophoneListener(on_segment=on_segment)
        mic.start()

        logger.info("robot-agent: microphone + VAD ready")
        await asyncio.Future()
    finally:
        if mic is not None:
            mic.stop()
        camera_launcher.stop()
        for future in list(pending_tasks):
            future.cancel()

        # 显式 shutdown rclpy，避免 GC 析构时的 segfault
        try:
            import rclpy as _rclpy
            if _rclpy.ok():
                _rclpy.try_shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # pending_tasks 的清理已在 main() 的 finally 块中完成
        logger.info("robot-agent: 用户中断执行 (KeyboardInterrupt)")
    finally:
        # 使用 os._exit 跳过 Python GC 析构阶段，
        # 避免 rclpy C++ 层对象析构顺序错误引发 segfault
        import os as _os
        _os._exit(0)
