"""
app.py - application entrypoint
"""

from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import CancelledError, Future

from src.robot_agent.bootstrap.logging import get_logger, setup_logging
from src.robot_agent.capabilities.asr.sherpa_adapter import SherpaRecognizer
from src.robot_agent.capabilities.asr.text_cleaner import DuplicateFilter
from src.robot_agent.graph.agent_graph import agent_graph
from src.robot_agent.graph.nodes.wake_guard import is_exit_text, is_stop_text
from src.robot_agent.graph.state import AgentState
from src.robot_agent.interfaces.audio.microphone import MicrophoneListener
from src.robot_agent.runtime import runtime_session
from src.robot_agent.settings import settings
from src.robot_agent.tools.registry import ToolRegistry

logger = get_logger(__name__)
SESSION_ID = "session_" + uuid.uuid4().hex[:8]


async def handle_asr_result(asr_text: str, emotion: str = "neutral") -> None:
    """Push one ASR result through the graph and persist wake-state changes."""
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
    """Start the robot runtime and keep listening for microphone segments."""
    setup_logging()
    logger.info("robot-agent: starting", lang=settings.lang, env=settings.env)

    ToolRegistry.get_instance()

    asr = SherpaRecognizer()
    asr.initialize()

    duplicate_filter = DuplicateFilter()
    loop = asyncio.get_running_loop()
    pending_tasks: set[Future] = set()

    def interrupt_tts(reason: str, text: str) -> None:
        """Interrupt current speech as quickly as possible."""
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
        """Handle one VAD audio segment."""
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

        if runtime_session.wake_state == "awake" and is_stop_text(cleaned_text, settings.lang):
            interrupt_tts(reason="stop_word", text=cleaned_text)
            return

        if runtime_session.wake_state == "awake" and is_exit_text(cleaned_text, settings.lang):
            # Interrupt current speech immediately, but still let wake_guard
            # generate the paired sleep acknowledgement in this turn.
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
