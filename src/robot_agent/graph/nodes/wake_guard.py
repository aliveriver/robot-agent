"""
nodes/wake_guard.py — 唤醒状态守卫节点

职责：
- 检查当前 wake_state
- 若处于 sleep 状态，判断输入是否为唤醒词
- 若处于 awake 状态，判断是否为退出词/停止词
- 返回更新后的 state，由 graph 决定下一个节点

注意：唤醒逻辑不走 LLM，用规则硬判断，保证实时性。
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState
from src.robot_agent.settings import settings

logger = get_logger(__name__)


def wake_guard(state: AgentState) -> dict:
    """
    唤醒守卫节点。

    - sleep 状态：仅处理唤醒词，其他输入丢弃
    - awake 状态：处理退出词 / 停止词，其他放行

    返回字典，LangGraph 会将其合并到 state 中。
    """
    text = state.normalized_text.strip().lower()
    lang = state.language
    wake_cfg = settings.wake

    # ── 根据语言选择词表 ──────────────────────────────────────
    wake_words = wake_cfg.words_cn if lang == "cn" else wake_cfg.words_en
    exit_words = wake_cfg.exit_words_cn if lang == "cn" else wake_cfg.exit_words_en
    stop_words = wake_cfg.stop_words_cn if lang == "cn" else wake_cfg.stop_words_en

    # ── 正规化比较（去掉标点，小写）────────────────────────────
    def _normalize(s: str) -> str:
        return "".join(ch for ch in s.lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")

    text_norm = _normalize(text)

    # ── 检查是否为唤醒词 ─────────────────────────────────────
    is_wake = any(_normalize(w) in text_norm for w in wake_words)
    # ── 检查是否为退出词 ─────────────────────────────────────
    is_exit = any(_normalize(w) in text_norm for w in exit_words)
    # ── 检查是否为停止词（打断）──────────────────────────────
    is_stop = any(_normalize(w) in text_norm for w in stop_words)

    updates: dict = {}

    if state.wake_state == "sleep":
        if is_wake:
            logger.info("wake_guard: robot awakened", text=text)
            updates["wake_state"] = "awake"
            # 唤醒响应由 response_generate 节点负责生成
            updates["response_text"] = ""
        else:
            # 休眠状态忽略非唤醒输入
            logger.debug("wake_guard: sleeping, ignoring input", text=text)
            updates["response_text"] = "__SKIP__"  # 特殊标记，graph 用于短路后续节点

    elif state.wake_state == "awake":
        if is_exit:
            logger.info("wake_guard: robot going to sleep", text=text)
            updates["wake_state"] = "sleep"
            updates["response_text"] = "__EXIT__"
        elif is_stop:
            logger.info("wake_guard: stop/interrupt detected", text=text)
            updates["interrupted"] = True
            updates["response_text"] = "__STOP__"
        # 否则正常放行，不修改 state

    return updates
