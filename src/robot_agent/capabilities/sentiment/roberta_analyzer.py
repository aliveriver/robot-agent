"""
capabilities/sentiment/roberta_analyzer.py - RoBERTa ONNX 文本情感分析

复用 tianyi_v1.py 中的 RoBERTa ONNX 7 分类情感分析模型，
提供独立的文本情感分析能力，与 SenseVoice ASR 标签级情感提取互补。

RoBERTa 基于文本语义做推理，SenseVoice 基于声学特征做推理，
两者可结合使用（如取高置信度的结果）。

主要接口:
    - `RobertaSentimentAnalyzer.analyze(text)` → str
    - `get_sentiment_analyzer()` → 进程级单例

用法:
    from src.robot_agent.capabilities.sentiment.roberta_analyzer import get_sentiment_analyzer

    analyzer = get_sentiment_analyzer()
    emotion = analyzer.analyze("今天天气真好")
    # => "happy"
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.asr.text_cleaner import EMOJI_DICT
from src.robot_agent.settings import settings

logger = get_logger(__name__)

EMOTION_7_LABELS: dict[int, str] = {
    0: "happy",
    1: "sad",
    2: "angry",
    3: "neutral",
    4: "fearful",
    5: "disgusted",
    6: "surprised",
}


class RobertaSentimentAnalyzer:
    """RoBERTa ONNX 7 分类文本情感分析器。

    模型结构与 tianyi_v1.py 中使用的完全一致：
    - tokenizer: AutoTokenizer (HuggingFace)
    - model: ONNX Runtime InferenceSession
    - 输入: input_ids + attention_mask (+ token_type_ids if present)
    - 输出: 7 分类 logits → argmax → emotion label
    """

    def __init__(self, model_dir: str) -> None:
        self._model_dir = Path(model_dir).expanduser()
        self._ort_session: Any | None = None
        self._tokenizer: Any | None = None
        self._initialized = False
        self._available = False

    def initialize(self) -> None:
        """加载 ONNX 模型和 tokenizer。"""
        if self._initialized:
            return

        self._initialized = True

        if not self._model_dir.exists():
            logger.warning(
                "RobertaSentimentAnalyzer: model dir not found, sentiment disabled",
                model_dir=str(self._model_dir),
            )
            return

        model_path = self._model_dir / "model.onnx"
        if not model_path.exists():
            logger.warning(
                "RobertaSentimentAnalyzer: model.onnx not found",
                model_dir=str(self._model_dir),
            )
            return

        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(str(self._model_dir))

            sess_options = ort.SessionOptions()
            sess_options.intra_op_num_threads = 1
            sess_options.inter_op_num_threads = 1
            self._ort_session = ort.InferenceSession(
                str(model_path), sess_options=sess_options
            )

            self._available = True
            logger.info(
                "RobertaSentimentAnalyzer: initialized",
                model_dir=str(self._model_dir),
            )
        except ImportError as exc:
            logger.warning(
                "RobertaSentimentAnalyzer: missing dependencies (onnxruntime/transformers)",
                error=str(exc),
            )
        except Exception as exc:
            logger.warning(
                "RobertaSentimentAnalyzer: initialization failed",
                error=str(exc),
            )

    @property
    def available(self) -> bool:
        """模型是否已成功加载并可用。"""
        if not self._initialized:
            self.initialize()
        return self._available

    def analyze(self, text: str) -> str:
        """分析文本情感，返回 7 分类标签之一。

        如果模型不可用，返回 "neutral"。
        """
        if not self._initialized:
            self.initialize()

        if not self._available:
            return "neutral"

        # 清理 SenseVoice 标签（与 tianyi_v1.py analyze_sentiment 一致）
        clean_text = text
        for tag in EMOJI_DICT:
            clean_text = clean_text.replace(tag, "")
        clean_text = clean_text.strip()

        if not clean_text:
            return "neutral"

        try:
            inputs = self._tokenizer(
                clean_text,
                return_tensors="np",
                padding="max_length",
                truncation=True,
                max_length=128,
            )
            ort_inputs: dict[str, Any] = {
                "input_ids": inputs["input_ids"].astype(np.int64),
                "attention_mask": inputs["attention_mask"].astype(np.int64),
            }
            if "token_type_ids" in inputs:
                ort_inputs["token_type_ids"] = inputs["token_type_ids"].astype(np.int64)

            ort_outs = self._ort_session.run(None, ort_inputs)
            logits = ort_outs[0]
            predicted_class_id = int(np.argmax(logits, axis=1)[0])
            emotion = EMOTION_7_LABELS.get(predicted_class_id, "neutral")

            logger.debug(
                "RobertaSentimentAnalyzer: analyzed",
                text=clean_text[:60],
                emotion=emotion,
                class_id=predicted_class_id,
            )
            return emotion
        except Exception as exc:
            logger.warning(
                "RobertaSentimentAnalyzer: analysis failed",
                error=str(exc),
                text=clean_text[:60],
            )
            return "neutral"


_analyzer_instance: RobertaSentimentAnalyzer | None = None
_analyzer_lock = threading.Lock()


def get_sentiment_analyzer() -> RobertaSentimentAnalyzer:
    """获取进程级单例情感分析器。"""
    global _analyzer_instance

    if _analyzer_instance is not None:
        return _analyzer_instance

    with _analyzer_lock:
        if _analyzer_instance is None:
            model_dir = getattr(settings, "sentiment", None)
            if model_dir and hasattr(model_dir, "model_dir"):
                path = model_dir.model_dir
            else:
                path = "./emotion_models/roberta_base_finetuned_model_onnx"
            _analyzer_instance = RobertaSentimentAnalyzer(model_dir=path)
        return _analyzer_instance
