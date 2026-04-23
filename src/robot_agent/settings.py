"""
settings.py — 统一配置加载入口

使用 pydantic-settings 从 .env 和 configs/app.yaml 两处加载配置。
环境变量优先级高于 yaml 文件。

用法:
    from src.robot_agent.settings import settings
    print(settings.llm.api_url)
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ── 根目录（robot-agent/）────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_YAML = ROOT_DIR / "configs" / "app.yaml"


# ─────────────────────────────────────────────────────────────
# 子配置块（从 yaml 解析后注入）
# ─────────────────────────────────────────────────────────────

class LLMSettings(BaseSettings):
    """LLM API 相关配置"""
    api_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o"
    multimodal_model: str = "gpt-4o"
    max_tokens: int = 512
    temperature: float = 0.7
    timeout: int = 30

    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=ROOT_DIR / ".env", extra="ignore")


class TTSSettings(BaseSettings):
    """TTS 服务配置"""
    provider: str = "kokoro"
    ws_url: str = "ws://127.0.0.1:7860/ws_rvc_tts"
    default_voice: str = "zh_spongebob"
    speed: float = 1.0

    model_config = SettingsConfigDict(env_prefix="KOKORO_", env_file=ROOT_DIR / ".env", extra="ignore")


class ASRSettings(BaseSettings):
    """ASR 服务配置"""
    provider: str = "sherpa"
    model_dir: str = ""
    tokens_path: str = ""

    model_config = SettingsConfigDict(env_prefix="SHERPA_", env_file=ROOT_DIR / ".env", extra="ignore")


class AudioSettings(BaseSettings):
    """音频设备与参数配置"""
    sample_rate: int = 16000
    channels: int = 1
    blocksize: int = 1600
    input_device: Optional[str] = None
    output_device: Optional[str] = None
    input_gain: float = 0.35
    noise_floor: float = 0.01
    speech_threshold: float = 0.03
    max_silence_sec: float = 0.45
    max_segment_sec: float = 8.0
    min_segment_sec: float = 0.3

    model_config = SettingsConfigDict(env_prefix="AUDIO_", env_file=ROOT_DIR / ".env", extra="ignore")


class WakeSettings(BaseSettings):
    """唤醒词与停止词配置（从 yaml 加载，不从 env 读取）"""
    words_cn: List[str] = ["你好罗比特", "你好，罗比特"]
    words_en: List[str] = ["hi robit", "hello robit"]
    exit_words_cn: List[str] = ["再见罗比特"]
    exit_words_en: List[str] = ["bye robit"]
    stop_words_cn: List[str] = ["停", "闭嘴", "安静"]
    stop_words_en: List[str] = ["stop", "quiet"]

    model_config = SettingsConfigDict(extra="ignore")


class MemorySettings(BaseSettings):
    """记忆系统参数"""
    short_term_max_turns: int = 15
    summary_every_n_turns: int = 8
    episodic_importance_threshold: float = 0.7

    model_config = SettingsConfigDict(extra="ignore")


class DatabaseSettings(BaseSettings):
    """数据库路径配置"""
    sqlite_path: str = "data/robot.db"
    chroma_persist_dir: str = "data/chroma"

    model_config = SettingsConfigDict(env_prefix="", env_file=ROOT_DIR / ".env", extra="ignore")


# ─────────────────────────────────────────────────────────────
# 顶层 Settings
# ─────────────────────────────────────────────────────────────

class Settings(BaseSettings):
    """
    全局配置对象，聚合所有子配置。
    优先从环境变量读取，其次从 configs/app.yaml。
    """
    lang: str = "cn"
    log_level: str = "INFO"
    env: str = "development"

    llm: LLMSettings = Field(default_factory=LLMSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)
    asr: ASRSettings = Field(default_factory=ASRSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    wake: WakeSettings = Field(default_factory=WakeSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_prefix="APP_",
        extra="ignore",
    )

    @classmethod
    def from_yaml(cls) -> "Settings":
        """从 yaml 文件加载并合并 env 覆盖"""
        yaml_data: dict = {}
        if CONFIG_YAML.exists():
            with open(CONFIG_YAML, encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f) or {}

        app_section = yaml_data.get("app", {})
        instance = cls(
            lang=app_section.get("lang", "cn"),
            log_level=app_section.get("log_level", "INFO"),
            env=app_section.get("env", "development"),
            wake=WakeSettings(**yaml_data.get("wake", {})),
            memory=MemorySettings(**yaml_data.get("memory", {})),
        )
        return instance


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回全局单例 Settings，首次调用时从 yaml + env 加载"""
    return Settings.from_yaml()


# 模块级别快捷访问
settings = get_settings()
