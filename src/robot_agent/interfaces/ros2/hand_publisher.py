"""
interfaces/ros2/hand_publisher.py - 灵巧手 ROS2 Publisher 封装

封装对以下 Topic 的发布逻辑（Inspire Hand，SDK 4.7 节）：
  - /inspire_hand/ctrl/left_hand   (左手)
  - /inspire_hand/ctrl/right_hand  (右手)

消息类型：sensor_msgs/msg/JointState
  工具层对外使用逻辑角度 0.0（张开）~ 1.0（合拢）
  实测控制 topic 的 position 方向相反，发布前会转换为 1.0（张开）~ 0.0（合拢）

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

# 手指通道 name（与 SDK JointState.name 完全一致，数字字符串）
# 顺序：1=小指, 2=无名指, 3=中指, 4=食指, 5=拇指弯曲, 6=拇指旋转
FINGER_NAMES: list[str] = ["1", "2", "3", "4", "5", "6"]

# 对外逻辑范围：0.0（张开）~ 1.0（合拢）。
# 实测 Inspire Hand 控制 topic 的 position 方向相反：1.0 为张开，0.0 为合拢。
ANGLE_MIN = 0.0
ANGLE_MAX = 1.0


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
            angles: 6 个手指的目标角度（逻辑值 0.0=张开 ~ 1.0=合拢）。
                    发布前会转换为设备 position：1.0=张开 ~ 0.0=合拢。
        """
        if len(angles) != 6:
            raise ValueError(f"angles 必须包含 6 个元素，当前为 {len(angles)}")

        # 夹紧到 0~1 范围
        clamped = [
            max(ANGLE_MIN, min(ANGLE_MAX, float(a)))
            for a in angles
        ]

        driver_positions = [ANGLE_MAX - a for a in clamped]

        logger.info(
            "HandPublisher.set_hand_angles",
            side=side,
            angles=[round(a, 3) for a in clamped],
            driver_positions=[round(p, 3) for p in driver_positions],
            sim=self._sim_mode,
        )

        if self._sim_mode:
            return

        msg = JointState()
        msg.header.stamp = self._node.get_clock().now().to_msg()
        msg.name     = FINGER_NAMES          # ["1","2","3","4","5","6"]
        msg.position = driver_positions

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
