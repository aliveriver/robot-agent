"""
capabilities/asr/text_cleaner.py — ASR 文本清洗工具

将 tianyi_v1.py 中的 format_str_v2、format_str_v3、
normalize_text_for_match、is_probable_self_echo 等函数迁移至此。

职责：
- 去除 SenseVoice 输出的情绪/语言标签
- 判断是否为自回声（机器人自己的声音被 ASR 识别到）
- 去重：短时间内重复内容过滤
"""

from __future__ import annotations

import difflib
import time

# ── 情绪 / 事件 / 语言标签字典（从 config.py 迁移）──────────
EMOJI_DICT: dict[str, str] = {
    "<|nospeech|><|Event_UNK|>": "❓",
    "<|zh|>": "", "<|en|>": "", "<|yue|>": "", "<|ja|>": "", "<|ko|>": "", "<|nospeech|>": "",
    "<|HAPPY|>": "😊", "<|SAD|>": "😔", "<|ANGRY|>": "😡", "<|NEUTRAL|>": "",
    "<|BGM|>": "🎼", "<|Speech|>": "", "<|Applause|>": "👏", "<|Laughter|>": "😀",
    "<|FEARFUL|>": "😰", "<|DISGUSTED|>": "🤢", "<|SURPRISED|>": "😮",
    "<|Cry|>": "😭", "<|EMO_UNKNOWN|>": "", "<|Sneeze|>": "🤧",
    "<|Breath|>": "", "<|Cough|>": "😷", "<|Sing|>": "",
    "<|Speech_Noise|>": "", "<|withitn|>": "", "<|woitn|>": "", "<|GBG|>": "", "<|Event_UNK|>": "",
}

EMO_DICT: dict[str, str] = {
    "<|HAPPY|>": "😊", "<|SAD|>": "😔", "<|ANGRY|>": "😡", "<|NEUTRAL|>": "",
    "<|FEARFUL|>": "😰", "<|DISGUSTED|>": "🤢", "<|SURPRISED|>": "😮",
}

EVENT_DICT: dict[str, str] = {
    "<|BGM|>": "🎼", "<|Speech|>": "", "<|Applause|>": "👏", "<|Laughter|>": "😀",
    "<|Cry|>": "😭", "<|Sneeze|>": "🤧", "<|Breath|>": "", "<|Cough|>": "🤧",
}

EMO_SET = {"😊", "😔", "😡", "😰", "🤢", "😮"}
EVENT_SET = {"🎼", "👏", "😀", "😭", "🤧", "😷"}


def extract_emotion(raw_text: str) -> str:
    """从 ASR 原始输出中提取情绪标签，默认返回 neutral"""
    for tag, emoji in EMO_DICT.items():
        if tag in raw_text:
            # 将 emoji 反查回情绪名称
            emoji_to_emotion = {v: k for k, v in {
                "😊": "happy", "😔": "sad", "😡": "angry",
                "😰": "fearful", "🤢": "disgusted", "😮": "surprised",
            }.items()}
            return emoji_to_emotion.get(emoji, "neutral")
    return "neutral"


def clean_asr_text(raw_text: str) -> str:
    """
    清洗 ASR 原始输出，移除所有特殊标签，返回纯文本。
    （对应 tianyi_v1.py format_str_v2 / format_str_v3）
    """
    text = raw_text
    for tag in EMOJI_DICT:
        text = text.replace(tag, "")
    return text.strip()


def normalize_for_match(text: str) -> str:
    """规范化文本用于相似度比较：去掉标点、转小写"""
    return "".join(ch.lower() for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def is_self_echo(asr_text: str, last_spoken_text: str, threshold: float = 0.72) -> bool:
    """
    判断 ASR 结果是否是机器人自己说话的回声。
    （对应 tianyi_v1.py is_probable_self_echo）
    """
    asr_norm = normalize_for_match(asr_text)
    spoken_norm = normalize_for_match(last_spoken_text)

    if not asr_norm or not spoken_norm or min(len(asr_norm), len(spoken_norm)) < 4:
        return False

    if asr_norm in spoken_norm or spoken_norm in asr_norm:
        return True

    return difflib.SequenceMatcher(None, asr_norm, spoken_norm).ratio() >= threshold


class DuplicateFilter:
    """
    短时间内重复输入过滤器。
    若相同文本在 window_sec 内再次出现，视为重复丢弃。
    """

    def __init__(self, window_sec: float = 2.0) -> None:
        self._last_text = ""
        self._last_time = 0.0
        self._window = window_sec

    def is_duplicate(self, text: str) -> bool:
        now = time.time()
        norm = normalize_for_match(text)
        if norm == normalize_for_match(self._last_text) and (now - self._last_time) < self._window:
            return True
        self._last_text = text
        self._last_time = now
        return False
