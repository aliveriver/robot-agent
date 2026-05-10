"""
interfaces/ros2/arm_subscriber.py - 机械臂状态 ROS2 Subscriber 封装

订阅 /arm/status 话题（bodyctrl_msgs/msg/MotorStatusMsg）。

MotorStatusMsg 实际结构（来自 ros_arm_probe.py）：
  msg.status : list[MotorStatus]
    item.name : int    电机 ID
    item.pos  : float  当前角度 (rad)
    item.spd  : float  当前速度 (rpm 或 rad/s，视 SDK 版本)
    item.tor  : float  当前力矩 (Nm)
    item.temperature : float  温度 (°C)（可能没有此字段）

关节布局（SDK 4.5 节，天轶 2.0 Pro）：
  左臂  Motor ID 11~17（从肩到腕）
  右臂  Motor ID 21~27（从肩到腕）
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

try:
    import rclpy
    from rclpy.node import Node
    _RCLPY_AVAILABLE = True
except ImportError:
    _RCLPY_AVAILABLE = False

try:
    from bodyctrl_msgs.msg import MotorStatusMsg
    _BODYCTRL_AVAILABLE = True
except ImportError:
    _BODYCTRL_AVAILABLE = False

_ROS2_AVAILABLE = _RCLPY_AVAILABLE and _BODYCTRL_AVAILABLE

from src.robot_agent.bootstrap.logging import get_logger

logger = get_logger(__name__)

# ── 电机 ID 分组 ────────────────────────────────────────────────
LEFT_ARM_IDS  = list(range(11, 18))  # 11~17
RIGHT_ARM_IDS = list(range(21, 28))  # 21~27

# 关节语义标签（索引 0~6 对应每侧臂从肩到腕）
JOINT_LABELS = [
    "肩俯仰(shoulder_pitch)",
    "肩侧摆(shoulder_roll)",
    "肩旋转(shoulder_yaw)",
    "肘弯曲(elbow)",
    "腕旋转(wrist_roll)",
    "腕俯仰(wrist_pitch)",
    "腕偏转(wrist_yaw)",
]


@dataclass
class ArmJointState:
    """单侧手臂的关节快照。"""
    motor_ids:    list[int]   = field(default_factory=list)
    positions:    list[float] = field(default_factory=list)   # rad
    speeds:       list[float] = field(default_factory=list)
    torques:      list[float] = field(default_factory=list)   # Nm
    temperatures: list[float] = field(default_factory=list)   # °C
    timestamp:    float       = 0.0


@dataclass
class BothArmsState:
    """双臂完整快照。"""
    left:     ArmJointState = field(default_factory=ArmJointState)
    right:    ArmJointState = field(default_factory=ArmJointState)
    is_valid: bool = False


class ArmSubscriber:
    """
    机械臂状态订阅器（进程级单例）。

    后台线程 spin 持续更新缓存；非 ROS2 环境退化为离线模式。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = BothArmsState()
        self._unavail_reason: str = ""

        if not _RCLPY_AVAILABLE:
            self._unavail_reason = "rclpy 未安装，请确认 ROS2 环境已 source"
            logger.warning("ArmSubscriber: rclpy 不可用，进入离线模式")
            self._sim_mode = True
            return

        if not _BODYCTRL_AVAILABLE:
            self._unavail_reason = (
                "bodyctrl_msgs 不可用，请在机器人工作空间编译并 source 后再启动"
            )
            logger.warning(
                "ArmSubscriber: bodyctrl_msgs 未编译/source，进入离线模式。"
                "相机正常工作说明 rclpy 已可用，可先在 ROS2 workspace 编译 bodyctrl_msgs"
            )
            self._sim_mode = True
            return

        self._sim_mode = False

        if not rclpy.ok():
            rclpy.init()

        self._node = Node("robot_agent_arm_subscriber")
        self._sub = self._node.create_subscription(
            MotorStatusMsg,
            "/arm/status",
            self._on_status,
            10,
        )

        self._spin_thread = threading.Thread(
            target=self._spin_loop, daemon=True, name="arm_status_spin"
        )
        self._spin_thread.start()
        logger.info("ArmSubscriber: 已启动后台订阅（/arm/status）")

    # ── 内部 ────────────────────────────────────────────────────

    def _spin_loop(self) -> None:
        try:
            rclpy.spin(self._node)
        except Exception as exc:  # noqa: BLE001
            logger.error("ArmSubscriber: spin 异常退出", error=str(exc))

    def _on_status(self, msg: "MotorStatusMsg") -> None:  # type: ignore[name-defined]
        """
        解析 MotorStatusMsg。

        实际结构（来自 ros_arm_probe.py）：
          for item in msg.status:
              motor_id = int(item.name)
              pos      = float(item.pos)
        """
        # 第一帧：打印字段帮助确认 SDK 结构
        if not self._state.is_valid:
            try:
                if hasattr(msg, "status") and msg.status:
                    sample = msg.status[0]
                    fields = [f for f in dir(sample) if not f.startswith("_")]
                    logger.info(
                        "ArmSubscriber: 首帧 MotorStatus item 字段",
                        fields=fields,
                        total_items=len(msg.status),
                    )
            except Exception:  # noqa: BLE001
                pass

        # 构建 motor_id -> index 映射
        items = getattr(msg, "status", [])
        if not items:
            logger.warning("ArmSubscriber: msg.status 为空，跳过本帧")
            return

        id_map: dict[int, "object"] = {}
        for item in items:
            try:
                mid = int(item.name)
                id_map[mid] = item
            except Exception:  # noqa: BLE001
                pass

        def _extract(ids: list[int]) -> ArmJointState:
            s = ArmJointState()
            s.timestamp = time.monotonic()
            for mid in ids:
                item = id_map.get(mid)
                s.motor_ids.append(mid)
                s.positions.append(   float(getattr(item, "pos",         0.0)) if item else 0.0)
                s.speeds.append(      float(getattr(item, "spd",         0.0)) if item else 0.0)
                s.torques.append(     float(getattr(item, "tor",         0.0)) if item else 0.0)
                s.temperatures.append(float(getattr(item, "temperature", 0.0)) if item else 0.0)
            return s

        left  = _extract(LEFT_ARM_IDS)
        right = _extract(RIGHT_ARM_IDS)

        with self._lock:
            self._state.left     = left
            self._state.right    = right
            self._state.is_valid = True

    # ── 公开方法 ────────────────────────────────────────────────

    def get_state(self) -> BothArmsState:
        """获取最新双臂状态快照（线程安全，返回深拷贝）。"""
        import copy
        with self._lock:
            return copy.deepcopy(self._state)

    def get_formatted_status(self) -> str:
        """
        返回面向 LLM 的可读状态字符串。
        不可用时返回带 [ARM_STATUS_UNAVAILABLE] 标记的技术描述。
        """
        state = self.get_state()

        if self._sim_mode or not state.is_valid:
            reason = self._unavail_reason or "尚未收到真实数据"
            return f"[ARM_STATUS_UNAVAILABLE] 原因：{reason}"

        now = time.monotonic()
        age = max(now - state.left.timestamp, now - state.right.timestamp)
        stale_warn = f"\n⚠️ 数据已 {age:.1f}s 未更新" if age > 2.0 else ""

        def _arm_lines(arm: ArmJointState, side_name: str) -> str:
            lines = [f"【{side_name}关节状态】"]
            for i, (mid, label) in enumerate(zip(arm.motor_ids, JOINT_LABELS)):
                pos  = arm.positions[i]    if i < len(arm.positions)    else 0.0
                spd  = arm.speeds[i]       if i < len(arm.speeds)       else 0.0
                tor  = arm.torques[i]      if i < len(arm.torques)      else 0.0
                temp = arm.temperatures[i] if i < len(arm.temperatures) else 0.0
                lines.append(
                    f"  J{i+1} {label:<32s}: "
                    f"pos={pos:+.3f}rad  spd={spd:+.2f}  tor={tor:+.2f}Nm  temp={temp:.1f}°C"
                )
            return "\n".join(lines)

        return (
            _arm_lines(state.left,  "左臂")
            + "\n\n"
            + _arm_lines(state.right, "右臂")
            + stale_warn
        )

    def destroy(self) -> None:
        if not self._sim_mode:
            self._node.destroy_node()
