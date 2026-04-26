"""
capabilities/asr/text_cleaner.py - ASR 文本清洗辅助工具

此模块将旧的 SenseVoice 标签清理、自身回声(self-echo)检测
以及短时间窗口内的重复过滤逻辑保留在一个地方。
"""

from __future__ import annotations

import difflib
import re
import time

EMOJI_DICT: dict[str, str] = {
    "<|nospeech|><|Event_UNK|>": "X",
    "<|zh|>": "",
    "<|en|>": "",
    "<|yue|>": "",
    "<|ja|>": "",
    "<|ko|>": "",
    "<|nospeech|>": "",
    "<|HAPPY|>": "happy_emoji",
    "<|SAD|>": "sad_emoji",
    "<|ANGRY|>": "angry_emoji",
    "<|NEUTRAL|>": "",
    "<|BGM|>": "bgm_emoji",
    "<|Speech|>": "",
    "<|Applause|>": "applause_emoji",
    "<|Laughter|>": "laughter_emoji",
    "<|FEARFUL|>": "fearful_emoji",
    "<|DISGUSTED|>": "disgusted_emoji",
    "<|SURPRISED|>": "surprised_emoji",
    "<|Cry|>": "cry_emoji",
    "<|EMO_UNKNOWN|>": "",
    "<|Sneeze|>": "sneeze_emoji",
    "<|Breath|>": "",
    "<|Cough|>": "cough_emoji",
    "<|Sing|>": "",
    "<|Speech_Noise|>": "",
    "<|withitn|>": "",
    "<|woitn|>": "",
    "<|GBG|>": "",
    "<|Event_UNK|>": "",
}

EMOTION_BY_TAG: dict[str, str] = {
    "<|HAPPY|>": "happy",
    "<|SAD|>": "sad",
    "<|ANGRY|>": "angry",
    "<|NEUTRAL|>": "neutral",
    "<|FEARFUL|>": "fearful",
    "<|DISGUSTED|>": "disgusted",
    "<|SURPRISED|>": "surprised",
}


def extract_emotion(raw_text: str) -> str:
    """在清理前从原始的 SenseVoice 输出中提取 emotion。"""
    for tag, emotion in EMOTION_BY_TAG.items():
        if tag in raw_text:
            return emotion
    return "neutral"


def clean_asr_text(raw_text: str) -> str:
    """移除 SenseVoice 控制标签并返回纯文本。"""
    text = raw_text
    for tag in EMOJI_DICT:
        text = text.replace(tag, "")
    return text.strip()
 
 
# 中英文及常用标点正则，与 tianyi_v1.py 保持一致
_ZH_EN_PUNCT_RE = re.compile(
    r'[\u4e00-\u9fffA-Za-z\u3000-\u303F\uFF00-\uFFEF\u2000-\u206F\u0020-\u007E]'
)


def is_valid_cjk_latin_text(text: str) -> bool:
    """检查 ASR 文本是否包含有效中英文字符，过滤纯噪声识别结果。"""
    return bool(_ZH_EN_PUNCT_RE.search(text))


def normalize_for_match(text: str) -> str:
    """标准化文本以便进行 self-echo 和重复内容匹配。"""
    return "".join(ch.lower() for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def is_self_echo(asr_text: str, last_spoken_text: str, threshold: float = 0.72) -> bool:
    """检查 ASR 文本是否可能是机器人听到的自身发出的声音。"""
    asr_norm = normalize_for_match(asr_text)
    spoken_norm = normalize_for_match(last_spoken_text)

    if not asr_norm or not spoken_norm or min(len(asr_norm), len(spoken_norm)) < 4:
        return False

    if asr_norm in spoken_norm or spoken_norm in asr_norm:
        return True

    return difflib.SequenceMatcher(None, asr_norm, spoken_norm).ratio() >= threshold


class DuplicateFilter:
    """在短时间窗口内丢弃重复的 ASR 结果。"""

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
