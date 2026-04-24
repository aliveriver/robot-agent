"""
nodes/speak_output.py - 语音播报节点

负责将 `AgentState.response_text` 交给 TTS 播放，并接入共享中断事件：

1. 过滤 `__SKIP__`、`__STOP__`、`__EXIT__` 等内部控制指令
2. 在正常播报前清理旧的中断标志
3. 将共享 `tts_interrupt_event` 传给 TTS，支持实时打断

用法:
    result = await speak_output(state)
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.tts.kokoro_adapter import get_tts
from src.robot_agent.graph.state import AgentState
from src.robot_agent.runtime import runtime_session

logger = get_logger(__name__)


async def speak_output(state: AgentState) -> dict:
    """将模型回复交给 TTS 播放。"""
    text = state.response_text.strip()

    if text in ("__SKIP__", "__STOP__", "__EXIT__", ""):
        logger.debug("speak_output: skipped", text=text)
        return {}

    if not runtime_session.allow_tts_playback(state.interrupt_revision):
        logger.info(
            "speak_output: stale response skipped after interrupt",
            text=text[:80],
            interrupt_revision=state.interrupt_revision,
            current_revision=runtime_session.get_interrupt_revision(),
        )
        return {}

    if runtime_session.should_skip_duplicate_tts(text):
        logger.info("speak_output: duplicate recent TTS skipped", text=text[:80])
        return {}

    logger.info("speak_output: speaking", text=text[:80], language=state.language)

    tts = get_tts()
    runtime_session.note_spoken_start(text)
    try:
        await tts.speak(
            text,
            lang=state.language,
            interrupt_event=runtime_session.tts_interrupt_event,
        )
    finally:
        runtime_session.note_spoken_finish()
    return {}
