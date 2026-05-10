"""
nodes/wake_guard.py - 唤醒/休眠守护节点

此节点决定当前输入是否应唤醒机器人、使其进入休眠、中断当前语音，
或者继续正常的图流程。
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
    """标准化命令文本，用于中英文关键词匹配。"""
    return "".join(
        ch for ch in value.lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
    )


def _contains_chinese(text: str) -> bool:
    """Return True when text contains at least one CJK unified ideograph."""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _contains_keyword(text: str, keywords: list[str]) -> bool:
    """判断文本是否包含关键词。"""
    text_norm = normalize_command_text(text)
    return any(normalize_command_text(word) in text_norm for word in keywords)


def is_wake_text(text: str, lang: str) -> bool:
    """返回文本是否包含唤醒词。"""
    wake_words = settings.wake.words_cn if lang == "cn" else settings.wake.words_en
    return _contains_keyword(text, wake_words)


def is_exit_text(text: str, lang: str) -> bool:
    """返回文本是否包含休眠/退出词。"""
    exit_words = settings.wake.exit_words_cn if lang == "cn" else settings.wake.exit_words_en
    return _contains_keyword(text, exit_words)


def is_stop_text(text: str, lang: str) -> bool:
    """返回文本是否包含停止/中断词。"""
    stop_words = settings.wake.stop_words_cn if lang == "cn" else settings.wake.stop_words_en
    return _contains_keyword(text, stop_words)


def _wake_ack(lang: str) -> str:
    """返回配置语言的唤醒确认文本。"""
    return WAKE_ACK_TEXT.get(lang, WAKE_ACK_TEXT["cn"])


def _sleep_ack(lang: str) -> str:
    """返回配置语言的休眠确认文本。"""
    return SLEEP_ACK_TEXT.get(lang, SLEEP_ACK_TEXT["cn"])


def wake_guard(state: AgentState) -> dict:
    """更新唤醒状态并在需要时生成控制响应。"""
    text = state.normalized_text.strip().lower()
    lang = state.language

    updates: dict = {}

    if settings.lang == "cn" and settings.ignore_non_chinese_input and not _contains_chinese(text):
        logger.info("wake_guard: non-Chinese input ignored", text=text[:80])
        updates["response_text"] = "__SKIP__"
        updates["normalized_text"] = ""
        return updates

    if state.wake_state == "sleep":
        if is_wake_text(text, lang):
            logger.info("wake_guard: robot awakened", text=text)
            updates["wake_state"] = "awake"
            updates["response_text"] = _wake_ack(lang)
            # 清除唤醒词，以免下游节点将其路由到 tools/LLM。
            updates["normalized_text"] = ""
        else:
            logger.debug("wake_guard: sleeping, ignoring input", text=text)
            updates["response_text"] = "__SKIP__"

    elif state.wake_state == "awake":
        if is_exit_text(text, lang):
            logger.info("wake_guard: robot going to sleep", text=text)
            updates["wake_state"] = "sleep"
            updates["response_text"] = _sleep_ack(lang)
            # 清除休眠词，以免下游节点将其路由到 tools/LLM。
            updates["normalized_text"] = ""
        elif is_stop_text(text, lang):
            logger.info("wake_guard: stop/interrupt detected", text=text)
            updates["interrupted"] = True
            updates["response_text"] = "__STOP__"

    return updates
