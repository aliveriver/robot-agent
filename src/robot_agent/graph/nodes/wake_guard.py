"""
nodes/wake_guard.py - 唤醒与休眠守卫节点

根据当前 `wake_state` 和配置中的词表判断本轮是否继续执行后续节点。
词表统一来自 `settings.wake`，不在代码中再维护第二套硬编码常量。

主要函数:
    - `is_wake_text(...)`：判断是否命中唤醒词
    - `is_exit_text(...)`：判断是否命中休眠词
    - `is_stop_text(...)`：判断是否命中打断词
    - `wake_guard(state)`：根据输入更新 `wake_state` 或返回控制指令
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState
from src.robot_agent.settings import settings

logger = get_logger(__name__)


def normalize_command_text(value: str) -> str:
    """统一清洗控制词输入，便于中英文关键词匹配。"""
    return "".join(
        ch for ch in value.lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
    )


def _contains_keyword(text: str, keywords: list[str]) -> bool:
    text_norm = normalize_command_text(text)
    return any(normalize_command_text(word) in text_norm for word in keywords)


def is_wake_text(text: str, lang: str) -> bool:
    """判断文本是否命中唤醒词。"""
    wake_words = settings.wake.words_cn if lang == "cn" else settings.wake.words_en
    return _contains_keyword(text, wake_words)


def is_exit_text(text: str, lang: str) -> bool:
    """判断文本是否命中休眠词。"""
    exit_words = settings.wake.exit_words_cn if lang == "cn" else settings.wake.exit_words_en
    return _contains_keyword(text, exit_words)


def is_stop_text(text: str, lang: str) -> bool:
    """判断文本是否命中打断词。"""
    stop_words = settings.wake.stop_words_cn if lang == "cn" else settings.wake.stop_words_en
    return _contains_keyword(text, stop_words)


def wake_guard(state: AgentState) -> dict:
    """按当前唤醒状态过滤输入，并决定是否切换 `wake_state`。"""
    text = state.normalized_text.strip().lower()
    lang = state.language

    updates: dict = {}

    if state.wake_state == "sleep":
        if is_wake_text(text, lang):
            logger.info("wake_guard: robot awakened", text=text)
            updates["wake_state"] = "awake"
            updates["response_text"] = ""
        else:
            logger.debug("wake_guard: sleeping, ignoring input", text=text)
            updates["response_text"] = "__SKIP__"

    elif state.wake_state == "awake":
        if is_exit_text(text, lang):
            logger.info("wake_guard: robot going to sleep", text=text)
            updates["wake_state"] = "sleep"
            updates["response_text"] = "__EXIT__"
        elif is_stop_text(text, lang):
            logger.info("wake_guard: stop/interrupt detected", text=text)
            updates["interrupted"] = True
            updates["response_text"] = "__STOP__"

    return updates
