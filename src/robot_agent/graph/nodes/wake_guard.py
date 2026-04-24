"""
nodes/wake_guard.py - 唤醒与休眠守卫节点

根据当前 `wake_state` 和输入文本判断本轮是否继续执行后续节点。
这里直接沿用 `tianyi_v1.py` 的固定控制词，不再从配置中读取：

1. 唤醒词
   中文: `你好`、`天轶`
   英文: `hello`、`hi`
2. 休眠词
   中文: `再见`、`休息`、`拜拜`
   英文: `goodbye`、`bye`、`rest`
3. 停止词
   中文: `停`、`别说了`、`闭嘴`、`安静`
   英文: `stop`、`quiet`
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState

logger = get_logger(__name__)

WAKE_WORDS_CN = ["你好", "天轶"]
WAKE_WORDS_EN = ["hello", "hi"]
EXIT_WORDS_CN = ["再见", "休息", "拜拜"]
EXIT_WORDS_EN = ["goodbye", "bye", "rest"]
STOP_WORDS_CN = ["停", "别说了", "闭嘴", "安静"]
STOP_WORDS_EN = ["stop", "quiet"]


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
    wake_words = WAKE_WORDS_CN if lang == "cn" else WAKE_WORDS_EN
    return _contains_keyword(text, wake_words)


def is_exit_text(text: str, lang: str) -> bool:
    """判断文本是否命中休眠词。"""
    exit_words = EXIT_WORDS_CN if lang == "cn" else EXIT_WORDS_EN
    return _contains_keyword(text, exit_words)


def is_stop_text(text: str, lang: str) -> bool:
    """判断文本是否命中停止词。"""
    stop_words = STOP_WORDS_CN if lang == "cn" else STOP_WORDS_EN
    return _contains_keyword(text, stop_words)


def wake_guard(state: AgentState) -> dict:
    """
    按当前唤醒态过滤输入，并决定是否切换 `wake_state`。
    """
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
