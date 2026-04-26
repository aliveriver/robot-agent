"""
capabilities/asr/text_cleaner.py - ASR text cleaning helpers

This module keeps the old SenseVoice tag cleanup, self-echo detection, and
short-window duplicate filtering logic in one place.
"""

from __future__ import annotations

import difflib
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
    """Extract emotion from raw SenseVoice output before cleanup."""
    for tag, emotion in EMOTION_BY_TAG.items():
        if tag in raw_text:
            return emotion
    return "neutral"


def clean_asr_text(raw_text: str) -> str:
    """Remove SenseVoice control tags and return plain text."""
    text = raw_text
    for tag in EMOJI_DICT:
        text = text.replace(tag, "")
    return text.strip()


def normalize_for_match(text: str) -> str:
    """Normalize text for self-echo and duplicate matching."""
    return "".join(ch.lower() for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def is_self_echo(asr_text: str, last_spoken_text: str, threshold: float = 0.72) -> bool:
    """Check whether ASR text is likely the robot hearing itself."""
    asr_norm = normalize_for_match(asr_text)
    spoken_norm = normalize_for_match(last_spoken_text)

    if not asr_norm or not spoken_norm or min(len(asr_norm), len(spoken_norm)) < 4:
        return False

    if asr_norm in spoken_norm or spoken_norm in asr_norm:
        return True

    return difflib.SequenceMatcher(None, asr_norm, spoken_norm).ratio() >= threshold


class DuplicateFilter:
    """Drop repeated ASR results inside a short time window."""

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
