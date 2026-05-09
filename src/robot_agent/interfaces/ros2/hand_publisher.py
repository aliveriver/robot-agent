"""
interfaces/ros2/hand_publisher.py - 灵巧手 ROS2 Publisher 封装

封装对以下 Topic 的发布逻辑（Inspire Hand，SDK 4.7 节）：
  - /inspire_hand/ctrl/left_hand   (左手)
  - /inspire_hand/ctrl/right_hand  (右手)

消息类型：sensor_msgs/msg/JointState
  position 字段范围：0（张开）~ 1000（合拢）

手指 ID / name 映射：
  1: 小指   (little)
  2: 无名指 (ring)
  3: 中指   (middle)
  4: 食指   (index)
  5: 拇指弯曲 (thumb_bend)
  6: 拇指旋转 (thumb_rot)
"""

from __future__ import annotations

import threading
from typing import Sequence

try:
    import rclpy
    from rclpy.node import Node
    from sensor_msgs.msg import JointState

    _ROS2_AVAILABLE = True
except ImportError:
    _ROS2_AVAILABLE = False

from src.robot_agent.bootstrap.logging import get_logger

logger = get_logger(__name__)

# 手指名称列表（与 SDK 中 JointState.name 对应）
FINGER_NAMES: list[str] = [
    "little",      # 小指    ID=1
    "ring",        # 无名指  ID=2
    "middle",      # 中指    ID=3
    "index",       # 食指    ID=4
    "thumb_bend",  # 拇指弯曲 ID=5
    "thumb_rot",   # 拇指旋转 ID=6
]

# SDK 角度范围
ANGLE_MIN = 0.0
ANGLE_MAX = 1000.0


class HandPublisher:
    """
    灵巧手 ROS2 接口封装。

    在 ROS2 环境可用时通过 Publisher 发送指令；
    否则进入模拟模式，仅记录日志。
    """

    def __init__(self) -> None:
        if not _ROS2_AVAILABLE:
            logger.warning("HandPublisher: rclpy/sensor_msgs 不可用，进入模拟模式")
            self._sim_mode = True
            return

        self._sim_mode = False
        self._lock = threading.Lock()

        if not rclpy.ok():
            rclpy.init()

        self._node = Node("robot_agent_hand_publisher")
        self._left_pub = self._node.create_publisher(
            JointState, "/inspire_hand/ctrl/left_hand", 10
        )
        self._right_pub = self._node.create_publisher(
            JointState, "/inspire_hand/ctrl/right_hand", 10
        )
        logger.info("HandPublisher: 初始化完成（ROS2 模式）")

    # ──────────────────────────────────────────────
    # 公开方法
    # ──────────────────────────────────────────────

    def set_hand_angles(
        self,
        side: str,
        angles: Sequence[float],
    ) -> None:
        """
        设置手指角度。

        Args:
            side:   "left" 或 "right"
            angles: 6 个手指的目标角度（归一化值 0.0=张开 ~ 1.0=合拢）
                    内部会乘以 1000 再发布（SDK 期望 0~1000）
        """
        if len(angles) != 6:
            raise ValueError(f"angles 必须包含 6 个元素，当前为 {len(angles)}")

        # 归一化转换为 SDK 原始值，并夹紧到合法范围
        raw = [
            max(ANGLE_MIN, min(ANGLE_MAX, a * ANGLE_MAX))
            for a in angles
        ]

        logger.info(
            "HandPublisher.set_hand_angles",
            side=side,
            angles=[round(a, 3) for a in angles],
            raw=[round(r, 1) for r in raw],
            sim=self._sim_mode,
        )

        if self._sim_mode:
            return

        msg = JointState()
        msg.name     = FINGER_NAMES
        msg.position = raw

        with self._lock:
            if side == "left":
                self._left_pub.publish(msg)
            elif side == "right":
                self._right_pub.publish(msg)
            else:
                raise ValueError(f"side 必须为 'left' 或 'right'，当前为 {side!r}")

    def set_both_hands(
        self,
        left_angles: Sequence[float],
        right_angles: Sequence[float],
    ) -> None:
        """同时控制左右双手。"""
        self.set_hand_angles("left", left_angles)
        self.set_hand_angles("right", right_angles)

    def destroy(self) -> None:
        """释放 ROS2 节点资源。"""
        if not self._sim_mode:
            self._node.destroy_node()
