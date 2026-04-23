"""
capabilities/asr/sherpa_adapter.py — Sherpa-ONNX ASR 适配器

将现有 tianyi_v1.py 中的 Sherpa 识别逻辑封装为独立模块。

TODO: 将原有 Sherpa 初始化和识别代码迁移到此类。
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.asr.base import ASRBase
from src.robot_agent.settings import settings

logger = get_logger(__name__)


class SherpaRecognizer(ASRBase):
    """Sherpa-ONNX 流式 ASR 适配器"""

    def __init__(self) -> None:
        self._recognizer = None
        self._language = settings.app.lang if hasattr(settings, "app") else settings.lang
        self._initialized = False

    def initialize(self) -> None:
        """
        初始化 Sherpa-ONNX 识别器。
        在应用启动时调用，而不是在每次识别时创建。

        TODO: 从 settings.asr.model_dir 加载模型
        """
        # TODO:
        # import sherpa_onnx
        # self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(...)
        logger.info("SherpaRecognizer: initialized (stub)", model_dir=settings.asr.model_dir)
        self._initialized = True

    def recognize(self, audio_frames: list) -> str:
        """
        将音频帧送入 Sherpa 识别，返回识别文本。

        TODO: 迁移 tianyi_v1.py 中 SenseVoiceSmall 识别逻辑
        """
        if not self._initialized:
            logger.warning("SherpaRecognizer: not initialized")
            return ""

        # TODO: 实现真实识别逻辑
        return ""

    def get_language(self) -> str:
        return self._language
