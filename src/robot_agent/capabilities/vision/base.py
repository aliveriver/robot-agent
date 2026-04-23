"""
capabilities/vision/base.py — 视觉能力抽象接口

定义摄像头/图像获取的标准协议。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class VisionBase(ABC):
    """视觉能力基类"""

    @abstractmethod
    async def capture_base64(self) -> str | None:
        """
        捕获当前帧并返回 base64 编码的 JPEG 图像。
        失败时返回 None。
        """
        ...
