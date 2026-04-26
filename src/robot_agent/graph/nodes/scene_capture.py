"""
scene_capture.py - 场景采图节点

负责在用户问题需要视觉上下文时抓取一帧图像，并写回 `state.scene_image_b64`。
抓图结果会在后续 `response_gen` 中自动以多模态消息的形式传给 LLM。

主要接口:
    - `scene_capture(state)`：按输入内容判断是否需要采图，并返回状态更新
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.vision.ros_camera import get_camera_provider
from src.robot_agent.graph.state import AgentState
from src.robot_agent.settings import settings

logger = get_logger(__name__)

VISUAL_KEYWORDS_CN = [
    "看看",
    "看一下",
    "看下",
    "前面",
    "周围",
    "画面",
    "图片",
    "镜头",
    "摄像头",
    "能看到",
]
VISUAL_KEYWORDS_EN = [
    "see",
    "look",
    "around",
    "in front",
    "what's there",
    "picture",
    "camera",
]


def _needs_visual_context(text: str, lang: str) -> bool:
    """判断当前输入是否需要摄像头画面。"""
    keywords = VISUAL_KEYWORDS_CN if lang == "cn" else VISUAL_KEYWORDS_EN
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in keywords)


async def scene_capture(state: AgentState) -> dict:
    """
    在需要视觉上下文时抓取场景图像。

    Returns:
        dict: 成功时返回 `{"scene_image_b64": ...}`，否则返回空字典。
    """
    should_capture = settings.vision.always_capture or _needs_visual_context(
        state.normalized_text,
        state.language,
    )
    if not should_capture:
        logger.debug("scene_capture: skipped (always_capture disabled and no visual keywords)")
        return {}

    logger.info(
        "scene_capture: capturing scene image",
        always_capture=settings.vision.always_capture,
    )
    image_b64 = await get_camera_provider().capture_base64()

    if image_b64 is None:
        logger.warning("scene_capture: failed to capture image")
        return {}

    logger.info("scene_capture: image captured", image_bytes=len(image_b64))
    return {"scene_image_b64": image_b64}
