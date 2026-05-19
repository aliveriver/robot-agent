"""
ros_bridge.py - ROS2 通信层

封装与机器人硬件的所有 ROS2 交互。
无 ROS2 环境时自动进入模拟模式（生成随机数据）。
"""

import math
import time
import random
import threading
from dataclasses import dataclass, field
from typing import Optional

# ROS2 导入（容错）
_ROS2_AVAILABLE = False
_ROS2_IMPORT_ERROR = ""
try:
    import glob as _glob
    import os as _os
    import sys as _sys
    _ROS2_WS = _os.environ.get("ROS2_WS", "/opt/PARTITIONS/A/ros2ws")
    _injected = []
    for _p in _glob.glob(f"{_ROS2_WS}/install/*/local/lib/python*/dist-packages"):
        if _p not in _sys.path:
            _sys.path.insert(0, _p)
            _injected.append(_p)
    if _injected:
        print(f"[ROS2] 注入路径: {len(_injected)} 个 ({_ROS2_WS})")

    import rclpy
    from rclpy.node import Node
    from bodyctrl_msgs.msg import MotorStatusMsg, CmdSetMotorPosition, SetMotorPosition
    from sensor_msgs.msg import JointState
    _ROS2_AVAILABLE = True
    print("[ROS2] 所有依赖导入成功")
except ImportError as e:
    _ROS2_IMPORT_ERROR = str(e)
    print(f"[ROS2] 导入失败: {e}")

LEFT_ARM_IDS = [11, 12, 13, 14, 15, 16, 17]
RIGHT_ARM_IDS = [21, 22, 23, 24, 25, 26, 27]
ALL_ARM_IDS = LEFT_ARM_IDS + RIGHT_ARM_IDS

JOINT_LABELS = [
    "肩俯仰", "肩侧摆", "肩旋转", "肘弯曲", "腕旋转", "腕俯仰", "腕偏转"
]


@dataclass
class BodySnapshot:
    left_arm: list = field(default_factory=lambda: [0.0] * 7)
    right_arm: list = field(default_factory=lambda: [0.0] * 7)
    left_hand: list = field(default_factory=lambda: [1.0] * 6)
    right_hand: list = field(default_factory=lambda: [1.0] * 6)
    timestamp: float = 0.0

    def to_dict(self):
        return {
            "left_arm": [round(v, 4) for v in self.left_arm],
            "right_arm": [round(v, 4) for v in self.right_arm],
            "left_hand": [round(v, 3) for v in self.left_hand],
            "right_hand": [round(v, 3) for v in self.right_hand],
            "timestamp": self.timestamp,
        }


class SimBridge:
    """模拟模式：无 ROS2 时提供假数据用于开发调试。"""

    def __init__(self):
        self.mode = "idle"
        self.joint_modes = {mid: "idle" for mid in ALL_ARM_IDS}
        self.current_config = {
            "big_joint": 3.0,
            "small_joint": 1.5,
        }
        self._hand_target = {"left": [1.0] * 6, "right": [1.0] * 6}
        self._t0 = time.time()

    def get_snapshot(self) -> BodySnapshot:
        t = time.time() - self._t0
        return BodySnapshot(
            left_arm=[0.1 * math.sin(t + i) for i in range(7)],
            right_arm=[0.1 * math.cos(t + i) for i in range(7)],
            left_hand=self._hand_target["left"][:],
            right_hand=self._hand_target["right"][:],
            timestamp=time.time(),
        )

    def set_mode(self, mode: str):
        self.mode = mode
        for mid in ALL_ARM_IDS:
            self.joint_modes[mid] = mode

    def set_joint_mode(self, motor_ids: list, mode: str):
        for mid in motor_ids:
            if mid in self.joint_modes:
                self.joint_modes[mid] = mode
        active_modes = set(self.joint_modes.values())
        if len(active_modes) == 1:
            self.mode = active_modes.pop()
        else:
            self.mode = "mixed"

    def set_current(self, motor_ids: list, current: float):
        pass

    def set_hand(self, side: str, angles: list):
        if side in ("left", "both"):
            self._hand_target["left"] = angles[:]
        if side in ("right", "both"):
            self._hand_target["right"] = angles[:]

    def send_frame(self, snapshot: BodySnapshot, speed: float = 10.0, current: float = 1.5):
        pass

    def destroy(self):
        pass


class RosBridge:
    """真实 ROS2 通信桥接。"""

    def __init__(self):
        if not _ROS2_AVAILABLE:
            raise RuntimeError("ROS2 不可用")

        if not rclpy.ok():
            rclpy.init()

        self._node = Node("trajectory_manager_node")
        self._lock = threading.Lock()

        # 状态订阅
        self._arm_pos = {mid: 0.0 for mid in ALL_ARM_IDS}
        self._hand_pos = {"left": [1.0] * 6, "right": [1.0] * 6}
        self._hand_target = {"left": [1.0] * 6, "right": [1.0] * 6}

        self._node.create_subscription(MotorStatusMsg, "/arm/status", self._arm_cb, 10)
        self._node.create_subscription(JointState, "/inspire_hand/state/left_hand", self._lhand_cb, 10)
        self._node.create_subscription(JointState, "/inspire_hand/state/right_hand", self._rhand_cb, 10)

        # 命令发布
        self._arm_pub = self._node.create_publisher(CmdSetMotorPosition, "/arm/cmd_pos", 10)
        self._lhand_pub = self._node.create_publisher(JointState, "/inspire_hand/ctrl/left_hand", 10)
        self._rhand_pub = self._node.create_publisher(JointState, "/inspire_hand/ctrl/right_hand", 10)

        self.mode = "idle"
        self.joint_modes = {mid: "idle" for mid in ALL_ARM_IDS}
        self.current_config = {"big_joint": 3.0, "small_joint": 1.5}
        self._hands_initialized = False

        # 控制循环 10Hz
        self._ctrl_timer = self._node.create_timer(0.1, self._ctrl_cb)

        # 后台 spin
        self._spin_thread = threading.Thread(target=self._spin, daemon=True)
        self._spin_thread.start()

    def _spin(self):
        rclpy.spin(self._node)

    def _arm_cb(self, msg):
        with self._lock:
            for item in msg.status:
                mid = int(item.name)
                if mid in ALL_ARM_IDS:
                    self._arm_pos[mid] = float(item.pos)

    def _lhand_cb(self, msg):
        if len(msg.position) >= 6:
            with self._lock:
                self._hand_pos["left"] = list(msg.position[:6])

    def _rhand_cb(self, msg):
        if len(msg.position) >= 6:
            with self._lock:
                self._hand_pos["right"] = list(msg.position[:6])

    def _ctrl_cb(self):
        if all(m == "idle" for m in self.joint_modes.values()):
            return

        arm_msg = CmdSetMotorPosition()
        arm_msg.header.stamp = self._node.get_clock().now().to_msg()
        arm_msg.header.frame_id = "trajectory_manager"
        arm_msg.cmds = []

        for mid in ALL_ARM_IDS:
            jmode = self.joint_modes[mid]
            if jmode == "idle":
                continue
            item = SetMotorPosition()
            item.name = mid
            if jmode == "limp":
                item.pos = self._arm_pos[mid]
                item.spd = 0.0
                item.cur = 0.0
            elif jmode == "lock":
                item.pos = self._arm_pos[mid]
                item.spd = 10.0
                if mid in [11, 12, 21, 22]:
                    item.cur = self.current_config["big_joint"]
                else:
                    item.cur = self.current_config["small_joint"]
            arm_msg.cmds.append(item)

        if arm_msg.cmds:
            self._arm_pub.publish(arm_msg)

        # 手部持续下发
        for side, pub in [("left", self._lhand_pub), ("right", self._rhand_pub)]:
            h_msg = JointState()
            h_msg.header.stamp = self._node.get_clock().now().to_msg()
            h_msg.name = ["1", "2", "3", "4", "5", "6"]
            h_msg.position = self._hand_target[side]
            pub.publish(h_msg)

    def get_snapshot(self) -> BodySnapshot:
        with self._lock:
            return BodySnapshot(
                left_arm=[self._arm_pos[mid] for mid in LEFT_ARM_IDS],
                right_arm=[self._arm_pos[mid] for mid in RIGHT_ARM_IDS],
                left_hand=self._hand_pos["left"][:],
                right_hand=self._hand_pos["right"][:],
                timestamp=time.time(),
            )

    def set_mode(self, mode: str):
        if mode == "limp" and not self._hands_initialized:
            with self._lock:
                self._hand_target["left"] = self._hand_pos["left"][:]
                self._hand_target["right"] = self._hand_pos["right"][:]
            self._hands_initialized = True
        self.mode = mode
        for mid in ALL_ARM_IDS:
            self.joint_modes[mid] = mode

    def set_joint_mode(self, motor_ids: list, mode: str):
        if mode == "limp" and not self._hands_initialized:
            with self._lock:
                self._hand_target["left"] = self._hand_pos["left"][:]
                self._hand_target["right"] = self._hand_pos["right"][:]
            self._hands_initialized = True
        for mid in motor_ids:
            if mid in self.joint_modes:
                self.joint_modes[mid] = mode
        active_modes = set(self.joint_modes.values())
        if len(active_modes) == 1:
            self.mode = active_modes.pop()
        else:
            self.mode = "mixed"

    def set_current(self, motor_ids: list, current: float):
        for mid in motor_ids:
            if mid in [11, 12, 21, 22]:
                self.current_config["big_joint"] = current
            else:
                self.current_config["small_joint"] = current

    def set_hand(self, side: str, angles: list):
        ratios = [max(0.0, min(1.0, v / 100.0 if v > 1.5 else v)) for v in angles]
        if side in ("left", "both"):
            self._hand_target["left"] = ratios[:]
        if side in ("right", "both"):
            self._hand_target["right"] = ratios[:]

    def send_frame(self, snapshot: BodySnapshot, speed: float = 10.0, current: float = 1.5):
        """回放时逐帧发送位置指令。"""
        arm_msg = CmdSetMotorPosition()
        arm_msg.header.stamp = self._node.get_clock().now().to_msg()
        arm_msg.header.frame_id = "trajectory_playback"
        arm_msg.cmds = []

        for i, mid in enumerate(LEFT_ARM_IDS):
            item = SetMotorPosition()
            item.name = mid
            item.pos = snapshot.left_arm[i]
            item.spd = speed
            item.cur = current if mid not in [11, 12] else self.current_config["big_joint"]
            arm_msg.cmds.append(item)

        for i, mid in enumerate(RIGHT_ARM_IDS):
            item = SetMotorPosition()
            item.name = mid
            item.pos = snapshot.right_arm[i]
            item.spd = speed
            item.cur = current if mid not in [21, 22] else self.current_config["big_joint"]
            arm_msg.cmds.append(item)

        self._arm_pub.publish(arm_msg)

        for side, pub, hand_data in [
            ("left", self._lhand_pub, snapshot.left_hand),
            ("right", self._rhand_pub, snapshot.right_hand),
        ]:
            h_msg = JointState()
            h_msg.header.stamp = self._node.get_clock().now().to_msg()
            h_msg.name = ["1", "2", "3", "4", "5", "6"]
            h_msg.position = hand_data
            pub.publish(h_msg)

    def destroy(self):
        self._node.destroy_node()


def create_bridge():
    """工厂函数：有 ROS2 用真实桥接，否则用模拟。"""
    if _ROS2_AVAILABLE:
        try:
            bridge = RosBridge()
            print("[ROS2] RosBridge 初始化成功，使用真实模式")
            return bridge
        except Exception as e:
            print(f"[WARN] ROS2 初始化失败，回退到模拟模式: {e}")
            return SimBridge()
    else:
        print(f"[INFO] ROS2 不可用（{_ROS2_IMPORT_ERROR}），使用模拟模式")
        return SimBridge()
