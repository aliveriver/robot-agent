"""
nodes/speak_output.py — TTS 播放节点

职责：
- 读取 state.response_text
- 调用 TTS 服务转语音
- 播放音频（支持中断）

TTS 实现在 capabilities/tts/ 中，这里只做调用。
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState

logger = get_logger(__name__)


async def speak_output(state: AgentState) -> dict:
    """
    TTS 播放节点（异步）。

    跳过特殊标记（__SKIP__ / __STOP__ / __EXIT__）。
    TODO: 接入真实 TTS 服务
    """
    text = state.response_text

    # ── 跳过内部控制标记 ─────────────────────────────────────
    if text in ("__SKIP__", "__STOP__", "__EXIT__", ""):
        logger.debug("speak_output: skipped", text=text)
        return {}

    logger.info("speak_output: speaking", text=text[:40])

    # TODO: from src.robot_agent.capabilities.tts.kokoro_adapter import KokoroTTS
    # TODO: await tts.speak(text, lang=state.language, interrupt_event=...)

    return {}
