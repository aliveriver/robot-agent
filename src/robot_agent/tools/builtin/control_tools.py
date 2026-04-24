"""
tools/builtin/control_tools.py - 控制类工具

提供会直接影响机器人运行态的工具：

1. `sleep_robot`：切换到休眠状态，并返回固定告别文案
2. `start_voice_clone`：调用内聚到 `robot-agent` 内部的声音克隆能力

这类工具可以直接回写 `wake_state`、`response_text` 等状态，
避免仅靠 LLM 二次组织语言。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.voice.voice_cloner import VoiceCloner
from src.robot_agent.graph.state import AgentState
from src.robot_agent.runtime import runtime_session
from src.robot_agent.settings import settings

logger = get_logger(__name__)

AGENT_ROOT = Path(__file__).resolve().parents[4]


async def sleep_robot(state: AgentState, reason: str = "", **kwargs: Any) -> dict:
    """让机器人进入休眠状态。"""
    runtime_session.request_tts_interrupt()

    message = "再见，有需要随时叫我哦。" if state.language == "cn" else "Goodbye, call me anytime."

    logger.info("sleep_robot: requested", reason=reason or state.normalized_text)
    return {
        "ok": True,
        "message": message,
        "reason": reason or state.normalized_text,
        "state_updates": {
            "wake_state": "sleep",
            "interrupted": True,
            "response_text": message,
        },
    }


async def start_voice_clone(
    state: AgentState,
    duration: int = 10,
    **kwargs: Any,
) -> dict:
    """
    启动声音克隆流程。

    参考 `tianyi_v1.py`：录制一段样本音频，然后上传到声音克隆服务。
    """
    sample_path = AGENT_ROOT / "data" / "voice_clone" / "user_voice_sample.wav"
    sample_path.parent.mkdir(parents=True, exist_ok=True)

    input_device = settings.audio.input_device or None

    def _run_voice_clone() -> tuple[bool, str]:
        try:
            cloner = VoiceCloner(device=input_device)
            record_ok = cloner.record_audio(str(sample_path), duration=duration)
            if not record_ok:
                return False, "record_failed"

            upload_ok = cloner.upload_voice(str(sample_path), language=state.language)
            if not upload_ok:
                return False, "upload_failed"

            return True, "ok"
        except Exception as exc:  # noqa: BLE001
            logger.exception("start_voice_clone: failed", error=str(exc))
            return False, str(exc)

    ok, detail = await asyncio.to_thread(_run_voice_clone)

    if ok:
        message = (
            "声音克隆已完成。现在你可以让我用你的声音说话。"
            if state.language == "cn"
            else "Voice cloning is ready. You can now ask me to speak with your voice."
        )
        logger.info("start_voice_clone: completed", duration=duration, sample_path=str(sample_path))
        return {
            "ok": True,
            "message": message,
            "sample_path": str(sample_path),
            "state_updates": {"response_text": message},
        }

    if detail == "record_failed":
        message = "录音失败，声音克隆没有完成。" if state.language == "cn" else "Recording failed."
    elif detail == "upload_failed":
        message = "样本上传失败，声音克隆没有完成。" if state.language == "cn" else "Upload failed."
    else:
        message = (
            f"声音克隆失败：{detail}"
            if state.language == "cn"
            else f"Voice cloning failed: {detail}"
        )

    return {
        "ok": False,
        "error": detail,
        "state_updates": {"response_text": message},
    }
