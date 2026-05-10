"""
settings.py - 统一配置加载入口

配置分工：
  configs/app.yaml  → 所有业务配置（URL、模型、参数、唤醒词等）
  .env              → 密钥 + 机器特定覆盖（AUDIO_INPUT_DEVICE 等）

加载优先级（高 → 低）：
  1. 系统环境变量
  2. .env 文件
  3. configs/app.yaml

用法:
    from src.robot_agent.settings import settings
    print(settings.llm.api_key)   # 来自 .env
    print(settings.llm.model)     # 来自 app.yaml
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT_DIR / ".env"
CONFIG_YAML = ROOT_DIR / "configs" / "app.yaml"

# 所有子配置类共用：从 .env 读取覆盖值，忽略多余字段
_ENV_CFG = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")


class LLMSettings(BaseSettings):
    """LLM 配置。api_key 来自 .env LLM_API_KEY，其余来自 app.yaml。"""

    api_url: str = Field(...)
    api_key: str = Field(...)
    model: str = Field(...)
    multimodal_model: str = Field(...)
    max_tokens: int = Field(...)
    temperature: float = Field(...)
    timeout: int = Field(...)

    model_config = SettingsConfigDict(env_prefix="LLM_", **_ENV_CFG)


class TTSSettings(BaseSettings):
    """TTS 配置。api_key 来自 .env TTS_API_KEY，其余来自 app.yaml。"""

    provider: str = Field(...)
    api_url: str = Field(...)
    ws_url: str = Field(...)
    api_key: str = Field(...)
    model: str = Field(...)
    default_voice: str = Field(...)
    speed: float = Field(...)

    model_config = SettingsConfigDict(env_prefix="TTS_", **_ENV_CFG)


class ASRSettings(BaseSettings):
    """ASR 配置。"""

    provider: str = Field(...)
    model_dir: str = Field(...)
    tokens_path: str = ""

    model_config = SettingsConfigDict(env_prefix="SHERPA_", **_ENV_CFG)


class AudioSettings(BaseSettings):
    """音频设备与阈值配置。input/output_device 可在 .env 中覆盖。"""

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

    model_config = SettingsConfigDict(env_prefix="AUDIO_", **_ENV_CFG)


class VisionSettings(BaseSettings):
    """视觉采图配置。"""

    camera_topic: str = Field(...)
    capture_timeout: float = Field(...)
    save_dir: str = Field(...)
    local_device_index: int = Field(...)
    prefer_ros: bool = Field(...)
    always_capture: bool = Field(True)
    auto_start_node: bool = Field(False)
    launch_command: str = Field("")
    launch_cwd: str = Field("")
    startup_delay_sec: float = Field(0.0)

    model_config = SettingsConfigDict(env_prefix="VISION_", **_ENV_CFG)


class VoiceCloneSettings(BaseSettings):
    """声音克隆服务配置。"""

    base_url: str = Field(...)

    model_config = SettingsConfigDict(env_prefix="VOICE_CLONE_", **_ENV_CFG)


class WakeSettings(BaseSettings):
    """唤醒词、休眠词和打断词配置。仅来自 app.yaml，不支持 env 覆盖。"""

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


class SentimentSettings(BaseSettings):
    """文本情感分析模型配置。"""

    enabled: bool = Field(True)
    model_dir: str = Field("./emotion_models/roberta_base_finetuned_model_onnx")

    model_config = SettingsConfigDict(extra="ignore")


class DatabaseSettings(BaseSettings):
    """数据库与持久化目录配置。"""

    sqlite_path: str = Field(...)
    chroma_persist_dir: str = Field(...)

    model_config = SettingsConfigDict(extra="ignore")


class Settings(BaseSettings):
    """全局配置对象（进程级单例，通过 get_settings() 获取）。"""

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
    sentiment: SentimentSettings = Field(default_factory=SentimentSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_prefix="APP_",
        extra="ignore",
    )

    @classmethod
    def from_yaml(cls) -> "Settings":
        """
        从 app.yaml 加载基础配置，.env 中的环境变量自动覆盖对应字段。

        构造流程：
          1. 读取 app.yaml 得到完整配置字典
          2. 将各节传入对应 Settings 子类（子类自动从 .env 读取覆盖）
          3. 返回组合好的全局 Settings 实例
        """
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
            sentiment=SentimentSettings(**yaml_data.get("sentiment", {})),
            database=DatabaseSettings(**yaml_data.get("database", {})),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回进程级单例配置。"""
    return Settings.from_yaml()


settings = get_settings()
