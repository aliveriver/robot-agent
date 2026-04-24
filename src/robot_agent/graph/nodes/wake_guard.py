"""
nodes/wake_guard.py - 唤醒与休眠守卫节点

根据当前 `wake_state` 和输入文本判断本轮是否继续执行后续节点：

1. `sleep` 状态下：
   只有命中唤醒词才切到 `awake` 并继续后续流程
2. `awake` 状态下：
   普通输入直接放行
   命中停止词时返回 `__STOP__`
   命中休眠词时切回 `sleep` 并返回 `__EXIT__`

这个节点本身只负责计算状态变化。
真正的状态持久化由 `app.py` 中的运行态在图执行完成后回写。
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState
from src.robot_agent.settings import settings

logger = get_logger(__name__)


def wake_guard(state: AgentState) -> dict:
    """
    按当前唤醒态过滤输入，并决定是否切换 wake_state。

    Returns:
        dict: 要写回图状态的字段，例如：
        - `wake_state`
        - `interrupted`
        - `response_text`
    """
    text = state.normalized_text.strip().lower()
    lang = state.language
    wake_cfg = settings.wake

    wake_words = wake_cfg.words_cn if lang == "cn" else wake_cfg.words_en
    exit_words = wake_cfg.exit_words_cn if lang == "cn" else wake_cfg.exit_words_en
    stop_words = wake_cfg.stop_words_cn if lang == "cn" else wake_cfg.stop_words_en

    def _normalize(value: str) -> str:
        return "".join(
            ch for ch in value.lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
        )

    text_norm = _normalize(text)
    is_wake = any(_normalize(word) in text_norm for word in wake_words)
    is_exit = any(_normalize(word) in text_norm for word in exit_words)
    is_stop = any(_normalize(word) in text_norm for word in stop_words)

    updates: dict = {}

    if state.wake_state == "sleep":
        if is_wake:
            logger.info("wake_guard: robot awakened", text=text)
            updates["wake_state"] = "awake"
            # 唤醒词本身不需要进入回复生成。
            updates["response_text"] = ""
        else:
            logger.debug("wake_guard: sleeping, ignoring input", text=text)
            updates["response_text"] = "__SKIP__"

    elif state.wake_state == "awake":
        if is_exit:
            logger.info("wake_guard: robot going to sleep", text=text)
            updates["wake_state"] = "sleep"
            updates["response_text"] = "__EXIT__"
        elif is_stop:
            logger.info("wake_guard: stop/interrupt detected", text=text)
            updates["interrupted"] = True
            updates["response_text"] = "__STOP__"

    return updates
