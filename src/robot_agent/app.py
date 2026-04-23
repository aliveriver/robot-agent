"""
app.py — 应用启动入口

初始化所有组件并启动机器人主循环：
1. 配置日志
2. 加载设置
3. 初始化能力层（ASR / TTS / Vision）
4. 初始化 Tool 注册表
5. 启动麦克风监听
6. 进入事件处理主循环（ASR 结果 → AgentGraph 执行）
"""

from __future__ import annotations

import asyncio
import uuid

from src.robot_agent.bootstrap.logging import get_logger, setup_logging
from src.robot_agent.graph.agent_graph import agent_graph
from src.robot_agent.graph.state import AgentState
from src.robot_agent.settings import settings
from src.robot_agent.tools.registry import ToolRegistry

logger = get_logger(__name__)


async def handle_asr_result(asr_text: str, emotion: str = "neutral") -> None:
    """
    处理一次 ASR 识别结果：构建 AgentState 并送入 Graph 执行。

    Args:
        asr_text: ASR 识别并清洗后的文本
        emotion:  情绪分类结果
    """
    state = AgentState(
        session_id="session_" + uuid.uuid4().hex[:8],
        user_id="default",
        input_text=asr_text,
        normalized_text=asr_text,  # TODO: 接入 text_cleaner
        language=settings.lang,
        emotion=emotion,
        wake_state="sleep",  # TODO: 从状态管理器读取真实状态
    )

    logger.info("app: invoking graph", input=asr_text[:40])
    result = await agent_graph.ainvoke(state)
    logger.info("app: graph done", response=result.get("response_text", "")[:40])


async def main() -> None:
    """应用主函数"""
    setup_logging()
    logger.info("robot-agent: starting", lang=settings.lang, env=settings.env)

    # ── 初始化 Tool 注册表 ────────────────────────────────────
    ToolRegistry.get_instance()

    # ── TODO: 初始化 ASR ────────────────────────────────────
    # from src.robot_agent.capabilities.asr.sherpa_adapter import SherpaRecognizer
    # asr = SherpaRecognizer()
    # asr.initialize()

    # ── TODO: 初始化 TTS ────────────────────────────────────
    # from src.robot_agent.capabilities.tts.kokoro_adapter import KokoroTTS
    # tts = KokoroTTS()

    # ── TODO: 初始化视觉 ────────────────────────────────────
    # from src.robot_agent.capabilities.vision.ros_camera import RosCameraProvider
    # camera = RosCameraProvider()
    # camera.start_subscriber()

    # ── TODO: 启动麦克风监听 ─────────────────────────────────
    # from src.robot_agent.interfaces.audio.microphone import MicrophoneListener
    # def on_segment(frames):
    #     text = asr.recognize(frames)
    #     if text:
    #         asyncio.run_coroutine_threadsafe(handle_asr_result(text), loop)
    # mic = MicrophoneListener(on_segment=on_segment)
    # mic.start()

    logger.info("robot-agent: framework ready (stubs in place, awaiting implementation)")

    # 临时：模拟一次对话测试框架是否跑通
    await handle_asr_result("你好罗比特")
    await handle_asr_result("你在看什么")

    logger.info("robot-agent: demo run complete")


if __name__ == "__main__":
    asyncio.run(main())
