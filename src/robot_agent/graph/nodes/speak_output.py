"""
nodes/speak_output.py - 输出语音节点

这个模块是 LangGraph 中的语音输出节点，负责：
1. 从 `AgentState.response_text` 读取模型最终回复
2. 过滤 `__SKIP__`、`__STOP__`、`__EXIT__` 等内部控制指令
3. 调用 TTS 适配层将文本播放为语音

主要接口：
- `speak_output(state)`：图节点入口，读取状态并触发语音输出

用法：
    result = await speak_output(state)
    # 节点执行完成后返回空字典，由后续节点继续处理
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.tts.kokoro_adapter import get_tts
from src.robot_agent.graph.state import AgentState

logger = get_logger(__name__)


async def speak_output(state: AgentState) -> dict:
    """将模型回复转换为语音输出。"""
    text = state.response_text.strip()

    if text in ("__SKIP__", "__STOP__", "__EXIT__", ""):
        logger.debug("speak_output: skipped", text=text)
        return {}

    logger.info("speak_output: speaking", text=text[:80], language=state.language)

    tts = get_tts()
    await tts.speak(text, lang=state.language)
    return {}
