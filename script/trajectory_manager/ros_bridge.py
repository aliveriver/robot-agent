"""
ros_bridge.py - ROS2 通信层

基于 control.py 和 record.py 的工作模式实现。
无 ROS2 环境时自动进入模拟模式。
"""

import math
import time
import threading
from dataclasses import dataclass, field

# ROS2 导入（容错）
_ROS2_AVAILABLE = False
_ROS2_IMPORT_ERROR = ""
try:
    import glob as _glob
    import os as _os
    import sys as _sys
    _ROS2_WS = _os.environ.get("ROS2_WS", "/opt/PARTITIONS/A/ros2ws")
    for _p in _glob.glob(f"{_ROS2_WS}/install/*/local/lib/python*/dist-packages"):
        if _p not in _sys.path:
            _sys.path.insert(0, _p)

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

# 来自 control.py — 每个关节的安全锁紧电流上限
SAFE_LOCK_CURRENT = {
    11: 6.0, 12: 5.0, 13: 4.0, 14: 4.0, 15: 2.0, 16: 2.0, 17: 2.0,
    21: 6.0, 22: 5.0, 23: 4.0, 24: 4.0, 25: 2.0, 26: 2.0, 27: 2.0,
}


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
    """模拟模式。"""

    def __init__(self):
        self.mode = "idle"
        self.joint_modes = {mid: "idle" for mid in ALL_ARM_IDS}
        self.current_config = {"big_joint": 2.0, "small_joint": 2.0}
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
        active = set(self.joint_modes.values())
        self.mode = active.pop() if len(active) == 1 else "mixed"

    def set_current(self, motor_ids: list, current: float):
        pass

    def set_hand(self, side: str, angles: list):
        if side in ("left", "both"):
            self._hand_target["left"] = angles[:]
        if side in ("right", "both"):
            self._hand_target["right"] = angles[:]

    def send_frame(self, snapshot: BodySnapshot, speed: float = 3.14, current: float = 2.0):
        pass

    def destroy(self):
        pass


# ── ROS2 真实实现 ──────────────────────────────────────────────

if _ROS2_AVAILABLE:
    class _BridgeNode(Node):
        """
        ROS2 节点 — 结构完全对齐 control.py 的 ManipulatorControlNode。
        """

        def __init__(self):
            super().__init__("trajectory_manager_node")

            # 订阅状态
            self.create_subscription(MotorStatusMsg, "/arm/status", self._arm_cb, 10)
            self.create_subscription(JointState, "/inspire_hand/state/left_hand", self._lhand_cb, 10)
            self.create_subscription(JointState, "/inspire_hand/state/right_hand", self._rhand_cb, 10)

            # 发布命令
            self.arm_cmd_pub = self.create_publisher(CmdSetMotorPosition, "/arm/cmd_pos", 10)
            self.lhand_cmd_pub = self.create_publisher(JointState, "/inspire_hand/ctrl/left_hand", 10)
            self.rhand_cmd_pub = self.create_publisher(JointState, "/inspire_hand/ctrl/right_hand", 10)

            # 状态
            self.current_arm_pos = {mid: 0.0 for mid in ALL_ARM_IDS}
            self.current_hand_pos = {"left": [1.0] * 6, "right": [1.0] * 6}
            self.locked_arm_pos = {mid: 0.0 for mid in ALL_ARM_IDS}
            self.target_hand_pos = {"left": [1.0] * 6, "right": [1.0] * 6}
            self.hands_initialized = False

            # 每个关节独立模式 (和 control.py 的 arm_mode 类似但更细粒度)
            self.joint_modes = {mid: "idle" for mid in ALL_ARM_IDS}

            # 控制循环 10Hz — 和 control.py 完全一致
            self.create_timer(0.1, self._ctrl_cb)

        def _arm_cb(self, msg):
            for item in msg.status:
                mid = int(item.name)
                if mid in self.current_arm_pos:
                    self.current_arm_pos[mid] = float(item.pos)

        def _lhand_cb(self, msg):
            if len(msg.position) >= 6:
                self.current_hand_pos["left"] = list(msg.position[:6])

        def _rhand_cb(self, msg):
            if len(msg.position) >= 6:
                self.current_hand_pos["right"] = list(msg.position[:6])

        def _ctrl_cb(self):
            """10Hz 控制回调 — 对齐 control.py 的 ctrl_cb。"""
            # 1. 手臂
            has_active = any(m != "idle" for m in self.joint_modes.values())
            if has_active:
                arm_msg = CmdSetMotorPosition()
                arm_msg.header.stamp = self.get_clock().now().to_msg()
                arm_msg.header.frame_id = "control_mode"
                arm_msg.cmds = []

                for mid in ALL_ARM_IDS:
                    jmode = self.joint_modes[mid]
                    if jmode == "idle":
                        continue
                    item = SetMotorPosition()
                    item.name = mid
                    if jmode == "limp":
                        item.pos = self.current_arm_pos[mid]
                        item.spd = 0.0
                        item.cur = 0.0
                    elif jmode == "lock":
                        item.pos = self.locked_arm_pos[mid]
                        item.spd = 3.14
                        item.cur = SAFE_LOCK_CURRENT.get(mid, 2.0)
                    arm_msg.cmds.append(item)

                if arm_msg.cmds:
                    self.arm_cmd_pub.publish(arm_msg)

            # 2. 手部 — 始终高频下发（和 control.py 一致）
            if self.hands_initialized:
                for side, pub in [("left", self.lhand_cmd_pub), ("right", self.rhand_cmd_pub)]:
                    h_msg = JointState()
                    h_msg.header.stamp = self.get_clock().now().to_msg()
                    h_msg.name = ["1", "2", "3", "4", "5", "6"]
                    h_msg.position = self.target_hand_pos[side]
                    pub.publish(h_msg)


class RosBridge:
    """对外接口，包装 _BridgeNode + spin 线程。启动方式和 control.py main() 一致。"""

    def __init__(self):
        if not _ROS2_AVAILABLE:
            raise RuntimeError("ROS2 不可用")

        rclpy.init()
        self._node = _BridgeNode()

        # 和 control.py / record.py 一样：后台线程 spin
        self._spin_thread = threading.Thread(
            target=rclpy.spin, args=(self._node,), daemon=True
        )
        self._spin_thread.start()

    @property
    def mode(self):
        active = set(self._node.joint_modes.values())
        return active.pop() if len(active) == 1 else "mixed"

    @property
    def joint_modes(self):
        return self._node.joint_modes

    @property
    def current_config(self):
        return {"big_joint": 6.0, "small_joint": 2.0}

    def get_snapshot(self) -> BodySnapshot:
        n = self._node
        return BodySnapshot(
            left_arm=[n.current_arm_pos[mid] for mid in LEFT_ARM_IDS],
            right_arm=[n.current_arm_pos[mid] for mid in RIGHT_ARM_IDS],
            left_hand=n.current_hand_pos["left"][:],
            right_hand=n.current_hand_pos["right"][:],
            timestamp=time.time(),
        )

    def set_mode(self, mode: str):
        n = self._node
        if mode == "limp" and not n.hands_initialized:
            n.target_hand_pos["left"] = n.current_hand_pos["left"][:]
            n.target_hand_pos["right"] = n.current_hand_pos["right"][:]
            n.hands_initialized = True
        if mode == "lock":
            for mid in ALL_ARM_IDS:
                n.locked_arm_pos[mid] = n.current_arm_pos[mid]
        print(f"[ROS2] set_mode -> {mode}")
        for mid in ALL_ARM_IDS:
            n.joint_modes[mid] = mode

    def set_joint_mode(self, motor_ids: list, mode: str):
        n = self._node
        if mode == "limp" and not n.hands_initialized:
            n.target_hand_pos["left"] = n.current_hand_pos["left"][:]
            n.target_hand_pos["right"] = n.current_hand_pos["right"][:]
            n.hands_initialized = True
        if mode == "lock":
            for mid in motor_ids:
                if mid in n.locked_arm_pos:
                    n.locked_arm_pos[mid] = n.current_arm_pos[mid]
        for mid in motor_ids:
            if mid in n.joint_modes:
                n.joint_modes[mid] = mode
        print(f"[ROS2] set_joint_mode: ids={motor_ids} mode={mode}")

    def set_current(self, motor_ids: list, current: float):
        # 动态修改 SAFE_LOCK_CURRENT
        for mid in motor_ids:
            SAFE_LOCK_CURRENT[mid] = current
        print(f"[ROS2] set_current: ids={motor_ids} cur={current}A")

    def set_hand(self, side: str, angles: list):
        n = self._node
        if not n.hands_initialized:
            n.target_hand_pos["left"] = n.current_hand_pos["left"][:]
            n.target_hand_pos["right"] = n.current_hand_pos["right"][:]
            n.hands_initialized = True
        ratios = [max(0.0, min(1.0, v / 100.0 if v > 1.5 else v)) for v in angles]
        if side in ("left", "both"):
            n.target_hand_pos["left"] = ratios[:]
        if side in ("right", "both"):
            n.target_hand_pos["right"] = ratios[:]

    def send_frame(self, snapshot: BodySnapshot, speed: float = 3.14, current: float = 2.0):
        """回放时逐帧发送位置指令。"""
        n = self._node
        arm_msg = CmdSetMotorPosition()
        arm_msg.header.stamp = n.get_clock().now().to_msg()
        arm_msg.header.frame_id = "trajectory_playback"
        arm_msg.cmds = []

        for i, mid in enumerate(LEFT_ARM_IDS):
            item = SetMotorPosition()
            item.name = mid
            item.pos = snapshot.left_arm[i]
            item.spd = speed
            item.cur = SAFE_LOCK_CURRENT.get(mid, current)
            arm_msg.cmds.append(item)

        for i, mid in enumerate(RIGHT_ARM_IDS):
            item = SetMotorPosition()
            item.name = mid
            item.pos = snapshot.right_arm[i]
            item.spd = speed
            item.cur = SAFE_LOCK_CURRENT.get(mid, current)
            arm_msg.cmds.append(item)

        n.arm_cmd_pub.publish(arm_msg)

        for side, pub, hand_data in [
            ("left", n.lhand_cmd_pub, snapshot.left_hand),
            ("right", n.rhand_cmd_pub, snapshot.right_hand),
        ]:
            h_msg = JointState()
            h_msg.header.stamp = n.get_clock().now().to_msg()
            h_msg.name = ["1", "2", "3", "4", "5", "6"]
            h_msg.position = hand_data
            pub.publish(h_msg)

    def destroy(self):
        self._node.destroy_node()
        rclpy.shutdown()


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
