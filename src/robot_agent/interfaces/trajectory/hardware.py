"""ROS2 轨迹硬件适配器；导入模块时不会强制依赖 ROS2。"""

from __future__ import annotations

import copy
import threading
import time
from typing import Any

LEFT_ARM_IDS = list(range(11, 18))
RIGHT_ARM_IDS = list(range(21, 28))
ALL_ARM_IDS = LEFT_ARM_IDS + RIGHT_ARM_IDS


class RosTrajectoryHardware:
    """缓存机器人关节状态，并发布录制回放所需的 ROS2 指令。"""

    def __init__(self) -> None:
        import rclpy
        from bodyctrl_msgs.msg import CmdSetMotorPosition, MotorStatusMsg, SetMotorPosition
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from sensor_msgs.msg import JointState

        if not rclpy.ok():
            rclpy.init()
        self._rclpy = rclpy
        self._CmdSetMotorPosition = CmdSetMotorPosition
        self._SetMotorPosition = SetMotorPosition
        self._JointState = JointState
        self._lock = threading.RLock()
        self._arms = {motor_id: 0.0 for motor_id in ALL_ARM_IDS}
        self._hands = {"left": [1.0] * 6, "right": [1.0] * 6}
        self._has_arm_frame = False
        self._node = Node("robot_agent_trajectory")
        self._node.create_subscription(MotorStatusMsg, "/arm/status", self._on_arm, 10)
        self._node.create_subscription(JointState, "/inspire_hand/state/left_hand", lambda msg: self._on_hand("left", msg), 10)
        self._node.create_subscription(JointState, "/inspire_hand/state/right_hand", lambda msg: self._on_hand("right", msg), 10)
        self._arm_pub = self._node.create_publisher(CmdSetMotorPosition, "/arm/cmd_pos", 10)
        self._left_hand_pub = self._node.create_publisher(JointState, "/inspire_hand/ctrl/left_hand", 10)
        self._right_hand_pub = self._node.create_publisher(JointState, "/inspire_hand/ctrl/right_hand", 10)
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self._node)
        self._spin_thread = threading.Thread(target=self._executor.spin, daemon=True, name="trajectory_ros_spin")
        self._spin_thread.start()

    def _on_arm(self, msg: Any) -> None:
        with self._lock:
            for item in getattr(msg, "status", []):
                motor_id = int(item.name)
                if motor_id in self._arms:
                    self._arms[motor_id] = float(item.pos)
            self._has_arm_frame = True

    def _on_hand(self, side: str, msg: Any) -> None:
        if len(msg.position) >= 6:
            with self._lock:
                self._hands[side] = [float(value) for value in msg.position[:6]]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            if not self._has_arm_frame:
                raise RuntimeError("尚未收到 /arm/status，无法开始录制")
            return {
                "arms": {str(key): value for key, value in self._arms.items()},
                "lhand": copy.deepcopy(self._hands["left"]),
                "rhand": copy.deepcopy(self._hands["right"]),
            }

    def set_teach_mode(self) -> None:
        """发送零速度、零电流命令，使双臂进入可拖动录制状态。"""
        frame = self.snapshot()
        self._publish_arm(frame["arms"], speed=0.0, current=0.0, frame_id="teach_mode")

    def publish_frame(self, frame: dict[str, Any], speed_scale: float) -> None:
        self._publish_arm(
            frame["arms"],
            speed=max(0.3, 3.14 * speed_scale),
            current=2.0,
            frame_id="trajectory_playback",
        )
        for side, key, publisher in (
            ("left", "lhand", self._left_hand_pub),
            ("right", "rhand", self._right_hand_pub),
        ):
            msg = self._JointState()
            msg.header.stamp = self._node.get_clock().now().to_msg()
            msg.name = ["1", "2", "3", "4", "5", "6"]
            msg.position = [float(value) for value in frame[key]]
            publisher.publish(msg)

    def _publish_arm(self, arms: dict[str, float], *, speed: float, current: float, frame_id: str) -> None:
        msg = self._CmdSetMotorPosition()
        msg.header.stamp = self._node.get_clock().now().to_msg()
        msg.header.frame_id = frame_id
        msg.cmds = []
        for motor_id, position in arms.items():
            item = self._SetMotorPosition()
            item.name = int(motor_id)
            item.pos = float(position)
            item.spd = float(speed)
            item.cur = float(current)
            msg.cmds.append(item)
        self._arm_pub.publish(msg)

    def wait_ready(self, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._has_arm_frame:
                    return
            time.sleep(0.05)
        raise RuntimeError("机器人关节状态未就绪，请确认 ROS2 驱动和 /arm/status")
