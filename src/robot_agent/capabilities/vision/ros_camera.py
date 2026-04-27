"""
ros_camera.py - 机器人摄像头采图适配器

负责在需要视觉上下文时抓取一帧 JPEG，并返回 base64 编码结果。
主路径参考 `tianyi_v1.py`，优先从 ROS2 图像 topic 订阅一帧；
如果当前环境没有 ROS2，则回退到本地 OpenCV 摄像头。

主要接口:
    - `RosCameraProvider.capture_base64()`：抓取一帧并返回 JPEG base64
    - `get_camera_provider()`：获取进程级单例相机实例

用法:
    from src.robot_agent.capabilities.vision.ros_camera import get_camera_provider

    camera = get_camera_provider()
    image_b64 = await camera.capture_base64()
"""

from __future__ import annotations

import asyncio
import base64
import threading
import time
from pathlib import Path

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.capabilities.vision.base import VisionBase
from src.robot_agent.settings import settings

logger = get_logger(__name__)


class RosCameraProvider(VisionBase):
    """优先通过 ROS2 抓图，必要时回退到本地 OpenCV 摄像头。"""

    def __init__(
        self,
        topic: str | None = None,
        timeout: float | None = None,
        save_dir: str | None = None,
        local_device_index: int | None = None,
        prefer_ros: bool | None = None,
    ) -> None:
        vision_cfg = settings.vision
        self._topic = topic or vision_cfg.camera_topic
        self._timeout = timeout or vision_cfg.capture_timeout
        self._save_dir = Path(save_dir or vision_cfg.save_dir)
        self._local_device_index = (
            vision_cfg.local_device_index if local_device_index is None else local_device_index
        )
        self._prefer_ros = vision_cfg.prefer_ros if prefer_ros is None else prefer_ros
        self._capture_lock = threading.Lock()
        self._ros_init_lock = threading.Lock()
        self._ros_initialized = False
        self._ros_node = None
        self._ros_spin_thread: threading.Thread | None = None
        self._ros_stop_event = threading.Event()

    async def capture_base64(self) -> str | None:
        """抓取一帧当前画面并返回 JPEG base64。"""
        return await asyncio.to_thread(self._capture_base64_blocking)

    def _capture_base64_blocking(self) -> str | None:
        with self._capture_lock:
            image_b64: str | None = None

            if self._prefer_ros:
                image_b64 = self._capture_from_ros()
                if image_b64:
                    return image_b64

            image_b64 = self._capture_from_local_camera()
            if image_b64:
                return image_b64

            logger.warning("RosCameraProvider: failed to capture image from ROS and local camera")
            return None

    def _capture_from_ros(self) -> str | None:
        try:
            import cv2
            from cv_bridge import CvBridge
            from rclpy.qos import qos_profile_sensor_data
            from sensor_msgs.msg import Image
        except ImportError as exc:
            logger.debug("RosCameraProvider: ROS capture unavailable", error=str(exc))
            return None

        node = self._ensure_ros_node()
        if node is None:
            return None

        bridge = CvBridge()
        frame_event = threading.Event()
        frame_container: dict[str, str] = {}

        def _img_cb(msg: Image) -> None:
            try:
                frame = bridge.imgmsg_to_cv2(msg, "bgr8")
                image_b64 = self._encode_frame(frame, cv2)
                if image_b64:
                    frame_container["image_b64"] = image_b64
            except Exception as exc:  # noqa: BLE001
                logger.exception("RosCameraProvider: ROS frame conversion failed", error=str(exc))
            finally:
                frame_event.set()

        # 贴近旧版 tianyi_v1.py：在一个长期存在、持续 spin 的节点上临时挂订阅，
        # 等到第一帧后立刻销毁订阅，而不是每次抓图都新建临时节点。
        subscription = node.create_subscription(
            Image,
            self._topic,
            _img_cb,
            qos_profile_sensor_data,
        )

        try:
            frame_event.wait(self._timeout)
        finally:
            try:
                node.destroy_subscription(subscription)
            except Exception:  # noqa: BLE001
                pass

        if not frame_event.is_set():
            logger.warning(
                "RosCameraProvider: ROS capture timed out",
                topic=self._topic,
                timeout=self._timeout,
            )
            return None

        image_b64 = frame_container.get("image_b64")
        if image_b64:
            logger.info("RosCameraProvider: captured frame from ROS", topic=self._topic)
        return image_b64

    def _ensure_ros_node(self):
        if self._ros_node is not None:
            return self._ros_node

        try:
            import rclpy
            from rclpy.node import Node
        except ImportError as exc:
            logger.debug("RosCameraProvider: ROS runtime unavailable", error=str(exc))
            return None

        with self._ros_init_lock:
            if self._ros_node is not None:
                return self._ros_node

            if not self._ros_initialized:
                try:
                    rclpy.init(args=None)
                except RuntimeError:
                    # ROS 上下文可能已由外部初始化，直接复用。
                    pass
                self._ros_initialized = True

            try:
                self._ros_node = Node("robot_agent_camera_capture")
            except Exception as exc:  # noqa: BLE001
                logger.warning("RosCameraProvider: failed to create ROS capture node", error=str(exc))
                self._ros_node = None
                return None

            self._ros_stop_event.clear()
            self._ros_spin_thread = threading.Thread(
                target=self._spin_ros_node,
                name="robot_agent_ros_camera_spin",
                daemon=True,
            )
            self._ros_spin_thread.start()
            logger.info("RosCameraProvider: ROS capture node ready", topic=self._topic)
            return self._ros_node

    def _spin_ros_node(self) -> None:
        try:
            import rclpy
        except ImportError:
            return

        while not self._ros_stop_event.is_set():
            node = self._ros_node
            if node is None:
                return

            try:
                rclpy.spin_once(node, timeout_sec=0.1)
            except Exception as exc:  # noqa: BLE001
                logger.debug("RosCameraProvider: ROS spin loop stopped", error=str(exc))
                return

    def _capture_from_local_camera(self) -> str | None:
        try:
            import cv2
        except ImportError as exc:
            logger.debug("RosCameraProvider: local OpenCV capture unavailable", error=str(exc))
            return None

        capture = cv2.VideoCapture(self._local_device_index)
        if not capture.isOpened():
            logger.warning(
                "RosCameraProvider: local camera open failed",
                device_index=self._local_device_index,
            )
            return None

        try:
            ok, frame = capture.read()
        finally:
            capture.release()

        if not ok or frame is None:
            logger.warning("RosCameraProvider: local camera frame read failed")
            return None

        image_b64 = self._encode_frame(frame, cv2)
        if image_b64:
            logger.info(
                "RosCameraProvider: captured frame from local camera",
                device_index=self._local_device_index,
            )
        return image_b64

    def _encode_frame(self, frame, cv2) -> str | None:
        self._save_dir.mkdir(parents=True, exist_ok=True)
        latest_path = self._save_dir / "latest.jpg"

        try:
            ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            if not ok:
                logger.warning("RosCameraProvider: JPEG encoding failed")
                return None

            jpeg_bytes = buffer.tobytes()
            latest_path.write_bytes(jpeg_bytes)
            return base64.b64encode(jpeg_bytes).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.exception("RosCameraProvider: frame encoding failed", error=str(exc))
            return None


_camera_instance: RosCameraProvider | None = None
_camera_lock = threading.Lock()


def get_camera_provider() -> RosCameraProvider:
    """获取进程级单例相机实例。"""
    global _camera_instance

    if _camera_instance is not None:
        return _camera_instance

    with _camera_lock:
        if _camera_instance is None:
            _camera_instance = RosCameraProvider()
        return _camera_instance
