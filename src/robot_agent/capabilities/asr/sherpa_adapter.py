"""
capabilities/asr/sherpa_adapter.py - Sherpa-ONNX recognizer adapter
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.asr.base import ASRBase
from src.robot_agent.capabilities.asr.text_cleaner import clean_asr_text, extract_emotion
from src.robot_agent.settings import settings

logger = get_logger(__name__)


@dataclass
class ASRRecognition:
    """One ASR recognition result with raw tags preserved."""

    raw_text: str
    cleaned_text: str
    emotion: str


class SherpaRecognizer(ASRBase):
    """Sherpa-ONNX offline ASR adapter."""

    def __init__(self) -> None:
        self._recognizer: Any | None = None
        self._language = settings.lang
        self._sample_rate = settings.audio.sample_rate
        self._initialized = False

    def initialize(self) -> None:
        """Load the Sherpa SenseVoice model."""
        if self._initialized:
            return

        try:
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError(
                "SherpaRecognizer 初始化失败: 未安装 sherpa_onnx"
            ) from exc

        model_path = self._resolve_model_path()
        tokens_path = self._resolve_tokens_path()

        try:
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=str(model_path),
                tokens=str(tokens_path),
                use_itn=True,
                debug=False,
                num_threads=1,
            )
        except Exception as exc:
            raise RuntimeError(f"SherpaRecognizer 初始化失败: {exc}") from exc

        self._initialized = True
        logger.info(
            "SherpaRecognizer: initialized",
            model_path=str(model_path),
            tokens_path=str(tokens_path),
            sample_rate=self._sample_rate,
        )

    def recognize(self, audio_frames: list) -> str:
        """Return the cleaned ASR text for compatibility with the base interface."""
        result = self.recognize_with_metadata(audio_frames)
        return result.cleaned_text if result else ""

    def recognize_with_metadata(self, audio_frames: list) -> ASRRecognition | None:
        """Return raw text, cleaned text, and extracted emotion in one pass."""
        if not self._initialized:
            self.initialize()

        audio_data = self._merge_audio_frames(audio_frames)
        if audio_data.size == 0:
            return None

        assert self._recognizer is not None

        try:
            stream = self._recognizer.create_stream()
            stream.accept_waveform(self._sample_rate, audio_data)
            self._recognizer.decode_stream(stream)
            raw_text = stream.result.text.strip()
        except Exception as exc:
            logger.exception("SherpaRecognizer: recognize failed", error=str(exc))
            return None

        if not raw_text:
            return None

        cleaned_text = clean_asr_text(raw_text)
        emotion = extract_emotion(raw_text)
        logger.info(
            "SherpaRecognizer: recognized",
            raw_text=raw_text,
            cleaned_text=cleaned_text,
            emotion=emotion,
        )
        return ASRRecognition(
            raw_text=raw_text,
            cleaned_text=cleaned_text,
            emotion=emotion,
        )

    def get_language(self) -> str:
        """Return the configured ASR language."""
        return self._language

    def _resolve_model_path(self) -> Path:
        model_dir = Path(settings.asr.model_dir).expanduser()
        if not settings.asr.model_dir:
            raise RuntimeError("SherpaRecognizer 初始化失败: 未配置 SHERPA_MODEL_DIR")
        if not model_dir.exists():
            raise RuntimeError(f"SherpaRecognizer 初始化失败: 模型目录不存在 {model_dir}")

        candidates = [
            model_dir / "model_quant.onnx",
            model_dir / "model.onnx",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate

        raise RuntimeError(
            f"SherpaRecognizer 初始化失败: 未找到 model_quant.onnx 或 model.onnx, model_dir={model_dir}"
        )

    def _resolve_tokens_path(self) -> Path:
        if settings.asr.tokens_path:
            tokens_path = Path(settings.asr.tokens_path).expanduser()
        else:
            tokens_path = Path(settings.asr.model_dir).expanduser() / "tokens.txt"

        if not tokens_path.exists():
            raise RuntimeError(
                f"SherpaRecognizer 初始化失败: tokens 文件不存在 {tokens_path}"
            )
        return tokens_path

    def _merge_audio_frames(self, audio_frames: list) -> np.ndarray:
        if not audio_frames:
            return np.array([], dtype=np.float32)

        chunks: list[np.ndarray] = []
        for frame in audio_frames:
            if frame is None:
                continue

            array = np.asarray(frame, dtype=np.float32)
            if array.ndim == 0:
                continue
            if array.ndim > 1:
                array = array.reshape(-1)
            if array.size == 0:
                continue
            chunks.append(array)

        if not chunks:
            return np.array([], dtype=np.float32)

        audio_data = np.concatenate(chunks).astype(np.float32, copy=False)
        return np.clip(audio_data, -1.0, 1.0)
