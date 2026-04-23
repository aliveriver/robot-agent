"""
capabilities/vision/ros_camera.py — ROS2 摄像头图像适配器

订阅 ROS2 图像 topic，在需要时取最新一帧并编码为 base64。

TODO: 将 tianyi_v1.py 中 ROS 图像订阅逻辑迁移至此。
"""

from __future__ import annotations

import base64

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.vision.base import VisionBase

logger = get_logger(__name__)


class RosCameraProvider(VisionBase):
    """
    ROS2 摄像头图像提供者。

    在后台订阅 /camera/image_raw（或配置的 topic），
    在 capture_base64 调用时返回最新帧。
    """

    def __init__(self, topic: str = "/camera/image_raw") -> None:
        self._topic = topic
        self._latest_frame = None  # numpy array or None
        self._initialized = False

    def start_subscriber(self) -> None:
        """
        启动 ROS2 图像 topic 订阅（在独立线程中运行）。

        TODO: 迁移 tianyi_v1.py 中 RobitImageSubscriber 逻辑
        """
        logger.info("RosCameraProvider: subscriber started (stub)", topic=self._topic)
        self._initialized = True

    async def capture_base64(self) -> str | None:
        """
        返回最新摄像头帧的 base64 编码字符串。

        TODO: 将 self._latest_frame (numpy) 编码为 JPEG base64
        """
        if self._latest_frame is None:
            logger.debug("RosCameraProvider: no frame available")
            return None

        # TODO:
        # import cv2
        # _, buf = cv2.imencode('.jpg', self._latest_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        # return base64.b64encode(buf).decode('utf-8')
        return None
