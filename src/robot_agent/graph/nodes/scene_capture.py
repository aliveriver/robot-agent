"""
nodes/scene_capture.py — 场景图像捕获节点（可选）

职责：
- 判断是否需要图像上下文（根据输入文本关键词）
- 若需要，从摄像头/ROS topic 获取当前帧
- 将图像编码为 base64 存入 state

不是每次对话都需要图像，只有用户问"看到了什么""前面有什么"等才触发。
"""

from __future__ import annotations

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState

logger = get_logger(__name__)

# 触发视觉捕获的关键词（可挪到 yaml 配置）
VISUAL_KEYWORDS_CN = ["看到", "前面", "周围", "图片", "拍", "看看", "什么东西"]
VISUAL_KEYWORDS_EN = ["see", "look", "around", "in front", "what's there", "picture", "camera"]


def _needs_visual_context(text: str, lang: str) -> bool:
    """简单关键词匹配，判断是否需要视觉上下文"""
    keywords = VISUAL_KEYWORDS_CN if lang == "cn" else VISUAL_KEYWORDS_EN
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)


async def scene_capture(state: AgentState) -> dict:
    """
    可选：捕获当前场景图像并存入 state。

    只有 _needs_visual_context 返回 True 时才触发，避免每轮都调用摄像头。

    TODO: 实现真实图像采集逻辑
    """
    if not _needs_visual_context(state.normalized_text, state.language):
        logger.debug("scene_capture: skipped (no visual keywords)")
        return {}  # 不修改 state

    logger.info("scene_capture: capturing scene image")

    # TODO: image_b64 = await ros_camera.capture_base64()
    # TODO: 或者用 cv2 直接读取摄像头帧
    image_b64: str | None = None

    if image_b64 is None:
        logger.warning("scene_capture: failed to capture image")
        return {}

    return {"scene_image_b64": image_b64}
