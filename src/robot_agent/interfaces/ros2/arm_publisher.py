"""
interfaces/ros2/arm_publisher.py - 机械臂 ROS2 Publisher 封装

发布到 /arm/cmd_pos（bodyctrl_msgs/msg/CmdSetMotorPosition），
每条指令是一个 SetMotorPosition 对象列表，包含 name/pos/spd/cur。

电机 ID 约定（SDK 4.5 节，天轶 2.0 Pro）：
  左臂: 11 ~ 17（7 关节，从肩到腕）
  右臂: 21 ~ 27（7 关节，从肩到腕）
"""

from __future__ import annotations

import threading
from typing import Sequence

try:
    import rclpy
    from rclpy.node import Node
    _RCLPY_AVAILABLE = True
except ImportError:
    _RCLPY_AVAILABLE = False

try:
    from bodyctrl_msgs.msg import CmdSetMotorPosition, SetMotorPosition
    _BODYCTRL_AVAILABLE = True
except ImportError:
    _BODYCTRL_AVAILABLE = False

_ROS2_AVAILABLE = _RCLPY_AVAILABLE and _BODYCTRL_AVAILABLE

from src.robot_agent.bootstrap.logging import get_logger

logger = get_logger(__name__)

# ── 电机 ID 常量 ────────────────────────────────────────────────
LEFT_ARM_IDS: list[int]  = list(range(11, 18))   # 11~17
RIGHT_ARM_IDS: list[int] = list(range(21, 28))   # 21~27
ARM_CURRENT_LIMITS: dict[int, float] = {
    **{motor_id: 8.0 for motor_id in (11, 12, 13, 14, 21, 22, 23, 24)},
    **{motor_id: 3.5 for motor_id in (15, 16, 17, 25, 26, 27)},
}


class ArmPublisher:
    """
    机械臂 ROS2 接口封装。

    发布到 /arm/cmd_pos，消息格式为 CmdSetMotorPosition：
      msg.cmds = [SetMotorPosition(name=<motor_id>, pos=<rad>, spd=<rpm>, cur=<A>), ...]
    """

    def __init__(self) -> None:
        if not _RCLPY_AVAILABLE:
            logger.warning("ArmPublisher: rclpy 不可用，进入离线模式")
            self._sim_mode = True
            return

        if not _BODYCTRL_AVAILABLE:
            logger.warning("ArmPublisher: bodyctrl_msgs 未编译/source，进入离线模式")
            self._sim_mode = True
            return

        self._sim_mode = False
        self._lock = threading.Lock()

        if not rclpy.ok():
            rclpy.init()

        self._node = Node("robot_agent_arm_publisher")
        self._cmd_pub = self._node.create_publisher(
            CmdSetMotorPosition, "/arm/cmd_pos", 10
        )
        logger.info("ArmPublisher: 初始化完成（ROS2 模式），topic=/arm/cmd_pos")

    # ── 公开方法 ────────────────────────────────────────────────

    def send_position(
        self,
        motor_ids: Sequence[int],
        positions: Sequence[float],
        speed_rpm: float = 10.0,
        current_a: float = 1.0,
        frame_id: str = "robot_agent",
    ) -> None:
        """
        向指定关节发送位置指令。

        Args:
            motor_ids:  电机 ID 列表（左臂 11~17，右臂 21~27）
            positions:  对应的目标角度列表（单位：rad）
            speed_rpm:  运动速度（转/分钟，默认 10.0，越小越慢越安全）
            current_a:  电流限制（安培，默认 1.0）
            frame_id:   消息 header.frame_id
        """
        logger.info(
            "ArmPublisher.send_position",
            motor_ids=list(motor_ids),
            positions=[round(p, 4) for p in positions],
            speed_rpm=speed_rpm,
            sim=self._sim_mode,
        )

        if self._sim_mode:
            raise RuntimeError("机械臂 ROS2 硬件适配未就绪，请先完成机器人自检")

        msg = CmdSetMotorPosition()
        msg.header.stamp = self._node.get_clock().now().to_msg()
        msg.header.frame_id = frame_id
        msg.cmds = []

        for motor_id, pos in zip(motor_ids, positions):
            item = SetMotorPosition()
            item.name = int(motor_id)
            item.pos  = float(pos)
            item.spd  = float(speed_rpm)
            item.cur  = float(ARM_CURRENT_LIMITS.get(int(motor_id), current_a))
            msg.cmds.append(item)

        with self._lock:
            self._cmd_pub.publish(msg)

    def set_zero(self) -> None:
        """双臂回零：向所有关节发送 pos=0.0。"""
        logger.info("ArmPublisher.set_zero", sim=self._sim_mode)
        all_ids = LEFT_ARM_IDS + RIGHT_ARM_IDS
        self.send_position(all_ids, [0.0] * len(all_ids), speed_rpm=5.0)

    def destroy(self) -> None:
        """释放 ROS2 节点资源。"""
        if not self._sim_mode:
            self._node.destroy_node()
