"""
interfaces/ros2/arm_publisher.py - 机械臂 ROS2 Publisher 封装

封装对以下 Topic 的发布逻辑，屏蔽 rclpy 细节供上层工具调用：
  - /arm/cmd_ctrl   (位置/混合模式)
  - /arm/cmd_vel    (速度模式)
  - /arm/cmd_set_zero (关节清零)

电机 ID 约定（来自 SDK 4.5 节）：
  左臂: 11 ~ 17（7 关节，从肩到腕）
  右臂: 21 ~ 27（7 关节，从肩到腕）
"""

from __future__ import annotations

import threading
from typing import Sequence

try:
    import rclpy
    from rclpy.node import Node

    # 自定义消息包（需在 ROS2 workspace 编译并 source 后可用）
    from bodyctrl_msgs.msg import CmdMotorCtrl, CmdSetMotorSpeed
    from std_msgs.msg import String

    _ROS2_AVAILABLE = True
except ImportError:
    _ROS2_AVAILABLE = False

from src.robot_agent.bootstrap.logging import get_logger

logger = get_logger(__name__)

# ──────────────────────────────────────────────
# 电机 ID 常量
# ──────────────────────────────────────────────
LEFT_ARM_IDS: list[int] = list(range(11, 18))   # 11 12 13 14 15 16 17
RIGHT_ARM_IDS: list[int] = list(range(21, 28))  # 21 22 23 24 25 26 27


class ArmPublisher:
    """
    机械臂 ROS2 接口封装。

    在 ROS2 环境可用时，通过 rclpy Publisher 发送指令；
    否则进入模拟模式（仅记录日志），方便在非机器人环境调试。
    """

    def __init__(self) -> None:
        if not _ROS2_AVAILABLE:
            logger.warning("ArmPublisher: rclpy/bodyctrl_msgs 不可用，进入模拟模式")
            self._sim_mode = True
            return

        self._sim_mode = False
        self._lock = threading.Lock()

        if not rclpy.ok():
            rclpy.init()

        self._node = Node("robot_agent_arm_publisher")
        self._cmd_ctrl_pub = self._node.create_publisher(
            CmdMotorCtrl, "/arm/cmd_ctrl", 10
        )
        self._cmd_vel_pub = self._node.create_publisher(
            CmdSetMotorSpeed, "/arm/cmd_vel", 10
        )
        self._zero_pub = self._node.create_publisher(
            String, "/arm/cmd_set_zero", 10
        )
        logger.info("ArmPublisher: 初始化完成（ROS2 模式）")

    # ──────────────────────────────────────────────
    # 公开方法
    # ──────────────────────────────────────────────

    def send_position(
        self,
        motor_ids: Sequence[int],
        positions: Sequence[float],
        kp: float = 100.0,
        kd: float = 2.0,
    ) -> None:
        """
        向指定关节发送位置指令（混合模式）。

        Args:
            motor_ids:  电机 ID 列表（左臂 11~17，右臂 21~27）
            positions:  对应的目标角度列表（单位：rad）
            kp:         位置增益（PD 控制器）
            kd:         阻尼增益（PD 控制器）
        """
        n = len(motor_ids)
        logger.info(
            "ArmPublisher.send_position",
            motor_ids=list(motor_ids),
            positions=[round(p, 4) for p in positions],
            sim=self._sim_mode,
        )

        if self._sim_mode:
            return

        msg = CmdMotorCtrl()
        msg.name = list(motor_ids)
        msg.kp   = [kp] * n
        msg.kd   = [kd] * n
        msg.pos  = list(positions)
        msg.spd  = [0.0] * n
        msg.tor  = [0.0] * n

        with self._lock:
            self._cmd_ctrl_pub.publish(msg)

    def send_velocity(
        self,
        motor_ids: Sequence[int],
        speeds: Sequence[float],
        current_limit: float = 5.0,
    ) -> None:
        """
        向指定关节发送速度指令。

        Args:
            motor_ids:     电机 ID 列表
            speeds:        对应的目标速度列表（单位：rad/s）
            current_limit: 电流限制（A）
        """
        n = len(motor_ids)
        logger.info(
            "ArmPublisher.send_velocity",
            motor_ids=list(motor_ids),
            speeds=[round(s, 4) for s in speeds],
            sim=self._sim_mode,
        )

        if self._sim_mode:
            return

        msg = CmdSetMotorSpeed()
        msg.name = list(motor_ids)
        msg.spd  = list(speeds)
        msg.cur  = [current_limit] * n

        with self._lock:
            self._cmd_vel_pub.publish(msg)

    def set_zero(self, target: str = "all") -> None:
        """
        发送关节清零指令。

        Args:
            target: 默认 "all"，也可以传特定 ID 字符串（参考 SDK 文档）
        """
        logger.info("ArmPublisher.set_zero", target=target, sim=self._sim_mode)

        if self._sim_mode:
            return

        with self._lock:
            self._zero_pub.publish(String(data=target))

    def destroy(self) -> None:
        """释放 ROS2 节点资源。"""
        if not self._sim_mode:
            self._node.destroy_node()
