"""
settings.py - 统一配置加载入口

只负责定义配置结构，并从 `.env` 和 `configs/app.yaml` 读取实际值。
环境变量优先级高于 yaml。除可选字段外，业务配置项都要求在配置文件中显式提供，
避免把运行参数写死在代码里形成第二套默认值。

用法:
    from src.robot_agent.settings import settings
    print(settings.llm.api_url)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_YAML = ROOT_DIR / "configs" / "app.yaml"


class LLMSettings(BaseSettings):
    """LLM 配置。"""

    api_url: str = Field(...)
    api_key: str = Field(...)
    model: str = Field(...)
    multimodal_model: str = Field(...)
    max_tokens: int = Field(...)
    temperature: float = Field(...)
    timeout: int = Field(...)

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=ROOT_DIR / ".env",
        extra="ignore",
    )


class TTSSettings(BaseSettings):
    """TTS 配置。"""

    provider: str = Field(...)
    api_url: str = Field(...)
    ws_url: str = Field(...)
    api_key: str = Field(...)
    model: str = Field(...)
    default_voice: str = Field(...)
    speed: float = Field(...)

    model_config = SettingsConfigDict(
        env_prefix="TTS_",
        env_file=ROOT_DIR / ".env",
        extra="ignore",
    )


class ASRSettings(BaseSettings):
    """ASR 配置。"""

    provider: str = Field(...)
    model_dir: str = Field(...)
    tokens_path: str = ""

    model_config = SettingsConfigDict(
        env_prefix="SHERPA_",
        env_file=ROOT_DIR / ".env",
        extra="ignore",
    )


class AudioSettings(BaseSettings):
    """音频设备与阈值配置。"""

    sample_rate: int = Field(...)
    channels: int = Field(...)
    blocksize: int = Field(...)
    input_device: Optional[str] = None
    output_device: Optional[str] = None
    input_gain: float = Field(...)
    noise_floor: float = Field(...)
    speech_threshold: float = Field(...)
    max_silence_sec: float = Field(...)
    max_segment_sec: float = Field(...)
    min_segment_sec: float = Field(...)

    model_config = SettingsConfigDict(
        env_prefix="AUDIO_",
        env_file=ROOT_DIR / ".env",
        extra="ignore",
    )


class VisionSettings(BaseSettings):
    """视觉采图配置。"""

    camera_topic: str = Field(...)
    capture_timeout: float = Field(...)
    save_dir: str = Field(...)
    local_device_index: int = Field(...)
    prefer_ros: bool = Field(...)

    model_config = SettingsConfigDict(
        env_prefix="VISION_",
        env_file=ROOT_DIR / ".env",
        extra="ignore",
    )


class VoiceCloneSettings(BaseSettings):
    """声音克隆服务配置。"""

    base_url: str = Field(...)

    model_config = SettingsConfigDict(
        env_prefix="VOICE_CLONE_",
        env_file=ROOT_DIR / ".env",
        extra="ignore",
    )


class WakeSettings(BaseSettings):
    """唤醒词、休眠词和打断词配置。"""

    words_cn: List[str] = Field(...)
    words_en: List[str] = Field(...)
    exit_words_cn: List[str] = Field(...)
    exit_words_en: List[str] = Field(...)
    stop_words_cn: List[str] = Field(...)
    stop_words_en: List[str] = Field(...)

    model_config = SettingsConfigDict(extra="ignore")


class MemorySettings(BaseSettings):
    """记忆系统配置。"""

    short_term_max_turns: int = Field(...)
    summary_every_n_turns: int = Field(...)
    episodic_importance_threshold: float = Field(...)

    model_config = SettingsConfigDict(extra="ignore")


class DatabaseSettings(BaseSettings):
    """数据库与持久化目录配置。"""

    sqlite_path: str = Field(...)
    chroma_persist_dir: str = Field(...)

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=ROOT_DIR / ".env",
        extra="ignore",
    )


class Settings(BaseSettings):
    """全局配置对象。"""

    lang: str = Field(...)
    log_level: str = Field(...)
    env: str = Field(...)

    llm: LLMSettings = Field(default_factory=LLMSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)
    asr: ASRSettings = Field(default_factory=ASRSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    vision: VisionSettings = Field(default_factory=VisionSettings)
    voice_clone: VoiceCloneSettings = Field(default_factory=VoiceCloneSettings)
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
        """从 yaml 读取配置，并允许 env 覆盖。"""
        yaml_data: dict = {}
        if CONFIG_YAML.exists():
            with open(CONFIG_YAML, encoding="utf-8") as handle:
                yaml_data = yaml.safe_load(handle) or {}

        app_section = yaml_data.get("app", {})
        return cls(
            lang=app_section["lang"],
            log_level=app_section["log_level"],
            env=app_section["env"],
            llm=LLMSettings(**yaml_data.get("llm", {})),
            tts=TTSSettings(**yaml_data.get("tts", {})),
            asr=ASRSettings(**yaml_data.get("asr", {})),
            audio=AudioSettings(**yaml_data.get("audio", {})),
            vision=VisionSettings(**yaml_data.get("vision", {})),
            voice_clone=VoiceCloneSettings(**yaml_data.get("voice_clone", {})),
            wake=WakeSettings(**yaml_data.get("wake", {})),
            memory=MemorySettings(**yaml_data.get("memory", {})),
            database=DatabaseSettings(**yaml_data.get("database", {})),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回进程级单例配置。"""
    return Settings.from_yaml()


settings = get_settings()
