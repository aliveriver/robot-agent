"""
nodes/wake_guard.py - Wake/sleep guard node

This node decides whether the current input should wake the robot, put it to
sleep, interrupt current speech, or continue through the normal graph flow.
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState
from src.robot_agent.settings import settings

logger = get_logger(__name__)

WAKE_ACK_TEXT = {
    "cn": "我在呢，有什么可以帮您？",
    "en": "I am here, how can I help you?",
}

SLEEP_ACK_TEXT = {
    "cn": "再见，有需要随时叫我哦。",
    "en": "Goodbye, call me anytime.",
}


def normalize_command_text(value: str) -> str:
    """Normalize command text for Chinese/English keyword matching."""
    return "".join(
        ch for ch in value.lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
    )


def _contains_keyword(text: str, keywords: list[str]) -> bool:
    text_norm = normalize_command_text(text)
    return any(normalize_command_text(word) in text_norm for word in keywords)


def is_wake_text(text: str, lang: str) -> bool:
    """Return whether the text contains a wake word."""
    wake_words = settings.wake.words_cn if lang == "cn" else settings.wake.words_en
    return _contains_keyword(text, wake_words)


def is_exit_text(text: str, lang: str) -> bool:
    """Return whether the text contains a sleep/exit word."""
    exit_words = settings.wake.exit_words_cn if lang == "cn" else settings.wake.exit_words_en
    return _contains_keyword(text, exit_words)


def is_stop_text(text: str, lang: str) -> bool:
    """Return whether the text contains a stop/interrupt word."""
    stop_words = settings.wake.stop_words_cn if lang == "cn" else settings.wake.stop_words_en
    return _contains_keyword(text, stop_words)


def _wake_ack(lang: str) -> str:
    """Return the wake acknowledgement text for the configured language."""
    return WAKE_ACK_TEXT.get(lang, WAKE_ACK_TEXT["cn"])


def _sleep_ack(lang: str) -> str:
    """Return the sleep acknowledgement text for the configured language."""
    return SLEEP_ACK_TEXT.get(lang, SLEEP_ACK_TEXT["cn"])


def wake_guard(state: AgentState) -> dict:
    """Update wake state and generate control responses when needed."""
    text = state.normalized_text.strip().lower()
    lang = state.language

    updates: dict = {}

    if state.wake_state == "sleep":
        if is_wake_text(text, lang):
            logger.info("wake_guard: robot awakened", text=text)
            updates["wake_state"] = "awake"
            updates["response_text"] = _wake_ack(lang)
            # Clear the wake words so downstream nodes do not route them into tools/LLM.
            updates["normalized_text"] = ""
        else:
            logger.debug("wake_guard: sleeping, ignoring input", text=text)
            updates["response_text"] = "__SKIP__"

    elif state.wake_state == "awake":
        if is_exit_text(text, lang):
            logger.info("wake_guard: robot going to sleep", text=text)
            updates["wake_state"] = "sleep"
            updates["response_text"] = _sleep_ack(lang)
            # Clear the sleep words so downstream nodes do not route them into tools/LLM.
            updates["normalized_text"] = ""
        elif is_stop_text(text, lang):
            logger.info("wake_guard: stop/interrupt detected", text=text)
            updates["interrupted"] = True
            updates["response_text"] = "__STOP__"

    return updates
