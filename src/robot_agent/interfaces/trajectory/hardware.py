"""ROS2 轨迹硬件适配器；导入模块时不会强制依赖 ROS2。"""

from __future__ import annotations

import copy
import threading
import time
from typing import Any

LEFT_ARM_IDS = list(range(11, 18))
RIGHT_ARM_IDS = list(range(21, 28))
ALL_ARM_IDS = LEFT_ARM_IDS + RIGHT_ARM_IDS
ARM_CURRENT_LIMITS = {
    **{motor_id: 8.0 for motor_id in (11, 12, 13, 14, 21, 22, 23, 24)},
    **{motor_id: 3.5 for motor_id in (15, 16, 17, 25, 26, 27)},
}


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
        self._has_hand_frame = {"left": False, "right": False}
        self._arm_modes = {"left": "idle", "right": "idle"}
        self._arm_lock_targets = {"left": {}, "right": {}}
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
        self._tension_thread = threading.Thread(target=self._tension_loop, daemon=True, name="arm_tension_loop")
        self._tension_thread.start()

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
                self._has_hand_frame[side] = True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            if not self._has_arm_frame:
                raise RuntimeError("尚未收到 /arm/status，无法开始录制")
            return {
                "arms": {str(key): value for key, value in self._arms.items()},
                "lhand": copy.deepcopy(self._hands["left"]),
                "rhand": copy.deepcopy(self._hands["right"]),
            }

    def current_joint_state(self, timeout: float = 2.0) -> dict[str, list[float]]:
        """返回机器人反馈话题中的真实双臂、双手关节位置。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._has_arm_frame and all(self._has_hand_frame.values()):
                    return {
                        "left": [self._arms[motor_id] for motor_id in LEFT_ARM_IDS],
                        "right": [self._arms[motor_id] for motor_id in RIGHT_ARM_IDS],
                        # 手驱动反馈为 1=松开、0=握紧；App/工具统一使用 0=松、1=紧。
                        "left_hand": [1.0 - value for value in self._hands["left"]],
                        "right_hand": [1.0 - value for value in self._hands["right"]],
                    }
            time.sleep(0.05)
        with self._lock:
            missing = []
            if not self._has_arm_frame:
                missing.append("/arm/status")
            if not self._has_hand_frame["left"]:
                missing.append("/inspire_hand/state/left_hand")
            if not self._has_hand_frame["right"]:
                missing.append("/inspire_hand/state/right_hand")
        raise RuntimeError(f"机器人关节反馈未就绪：{', '.join(missing)}")

    def set_arm_tension(self, side: str, tight: bool) -> dict[str, Any]:
        """记录模式；后台以 10Hz 持续松弛或锁紧指定手臂。"""
        if side not in {"left", "right"}:
            raise ValueError("side 必须是 left 或 right")
        frame = self.snapshot()
        ids = LEFT_ARM_IDS if side == "left" else RIGHT_ARM_IDS
        positions = {str(motor_id): frame["arms"][str(motor_id)] for motor_id in ids}
        with self._lock:
            self._arm_lock_targets[side] = positions
            self._arm_modes[side] = "tight" if tight else "relax"
        return {"ok": True, "side": side, "tight": tight}

    def clear_arm_tension(self) -> None:
        with self._lock:
            self._arm_modes = {"left": "idle", "right": "idle"}

    def _tension_loop(self) -> None:
        while True:
            with self._lock:
                modes = dict(self._arm_modes)
                targets = copy.deepcopy(self._arm_lock_targets)
                current = {str(key): value for key, value in self._arms.items()}
            for side, mode in modes.items():
                if mode == "idle":
                    continue
                ids = LEFT_ARM_IDS if side == "left" else RIGHT_ARM_IDS
                positions = (
                    targets[side]
                    if mode == "tight"
                    else {str(motor_id): current[str(motor_id)] for motor_id in ids}
                )
                self._publish_arm(
                    positions,
                    speed=3.14 if mode == "tight" else 0.0,
                    current=None if mode == "tight" else 0.0,
                    frame_id=f"{side}_arm_{mode}",
                )
            time.sleep(0.1)

    def set_teach_mode(self) -> None:
        """发送零速度、零电流命令，使双臂进入可拖动录制状态。"""
        self.clear_arm_tension()
        frame = self.snapshot()
        self._publish_arm(frame["arms"], speed=0.0, current=0.0, frame_id="teach_mode")

    def publish_frame(self, frame: dict[str, Any], speed_scale: float) -> None:
        self._publish_arm(
            frame["arms"],
            speed=max(0.3, 3.14 * speed_scale),
            current=None,
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

    def _publish_arm(self, arms: dict[str, float], *, speed: float, current: float | None, frame_id: str) -> None:
        msg = self._CmdSetMotorPosition()
        msg.header.stamp = self._node.get_clock().now().to_msg()
        msg.header.frame_id = frame_id
        msg.cmds = []
        for motor_id, position in arms.items():
            item = self._SetMotorPosition()
            item.name = int(motor_id)
            item.pos = float(position)
            item.spd = float(speed)
            item.cur = float(ARM_CURRENT_LIMITS[int(motor_id)] if current is None else current)
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
