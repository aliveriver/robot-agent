"""
app.py - 应用启动入口

负责初始化日志、工具注册、ASR、麦克风监听与 LangGraph 主流程。
这个模块还维护进程级运行态，确保同一次启动中的对话共享：

1. 固定 `session_id`，用于多轮记忆召回
2. 持续 `wake_state`，机器人被唤醒后保持 `awake`，
   直到命中休眠词才切回 `sleep`

用法:
    python -m src.robot_agent.app
"""

from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field

from src.robot_agent.bootstrap.logging import get_logger, setup_logging
from src.robot_agent.capabilities.asr.sherpa_adapter import SherpaRecognizer
from src.robot_agent.capabilities.asr.text_cleaner import DuplicateFilter, extract_emotion
from src.robot_agent.graph.agent_graph import agent_graph
from src.robot_agent.graph.state import AgentState
from src.robot_agent.interfaces.audio.microphone import MicrophoneListener
from src.robot_agent.settings import settings
from src.robot_agent.tools.registry import ToolRegistry

logger = get_logger(__name__)
SESSION_ID = "session_" + uuid.uuid4().hex[:8]


@dataclass
class RuntimeSession:
    """保存进程内持续状态，避免每轮对话都重置唤醒态。"""

    wake_state: str = "sleep"
    graph_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


runtime_session = RuntimeSession()


async def handle_asr_result(asr_text: str, emotion: str = "neutral") -> None:
    """
    处理一段 ASR 文本，并将其送入 AgentGraph。

    执行前会读取当前进程内的 `wake_state`；
    执行后会根据图返回结果回写最新 `wake_state`，
    以实现“唤醒一次后持续对话，直到休眠词出现”。

    Args:
        asr_text: 当前语音分段的识别结果
        emotion: 从文本中提取的情绪标签
    """
    # 用串行锁保证多段语音按到达顺序更新会话状态，
    # 避免并发写入导致 wake_state 被旧结果覆盖。
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
        )

        logger.info(
            "app: invoking graph",
            input=asr_text[:80],
            emotion=emotion,
            wake_state=current_wake_state,
        )
        result = await agent_graph.ainvoke(state)

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
    """启动应用主循环，并持续监听麦克风语音分段。"""
    setup_logging()
    logger.info("robot-agent: starting", lang=settings.lang, env=settings.env)

    ToolRegistry.get_instance()

    asr = SherpaRecognizer()
    asr.initialize()

    duplicate_filter = DuplicateFilter()
    loop = asyncio.get_running_loop()
    pending_tasks: set[Future] = set()

    def on_segment(frames) -> None:
        """处理一段通过 VAD 切出的原始音频帧。"""
        try:
            raw_text = asr.recognize(frames)
        except Exception as exc:
            logger.exception("app: ASR failed", error=str(exc))
            return

        if not raw_text:
            return

        if duplicate_filter.is_duplicate(raw_text):
            logger.debug("app: duplicate ASR text skipped", text=raw_text)
            return

        emotion = extract_emotion(raw_text)
        future = asyncio.run_coroutine_threadsafe(
            handle_asr_result(raw_text, emotion=emotion),
            loop,
        )
        pending_tasks.add(future)

        def cleanup(done_future: Future) -> None:
            pending_tasks.discard(done_future)
            try:
                done_future.result()
            except Exception as exc:
                logger.exception("app: handle_asr_result failed", error=str(exc))

        future.add_done_callback(cleanup)

    mic = MicrophoneListener(on_segment=on_segment)
    mic.start()

    logger.info("robot-agent: microphone + VAD ready")

    try:
        await asyncio.Future()
    finally:
        mic.stop()
        for future in list(pending_tasks):
            future.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("robot-agent: stopped by keyboard interrupt")
