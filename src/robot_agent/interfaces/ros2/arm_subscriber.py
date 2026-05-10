"""
interfaces/ros2/arm_subscriber.py - 机械臂状态 ROS2 Subscriber 封装

订阅 /arm/status 话题（bodyctrl_msgs/msg/MotorStatusMsg），
在后台持续缓存最新状态，供工具函数随时查询。

关节布局（SDK 4.5 节，天轶 2.0 Pro）：
  左臂  Motor ID 11~17：
    11 = 左肩俯仰 (shoulder pitch)
    12 = 左肩侧摆 (shoulder roll)
    13 = 左肩旋转 (shoulder yaw)
    14 = 左肘弯曲 (elbow)
    15 = 左腕旋转 (wrist roll)
    16 = 左腕俯仰 (wrist pitch)
    17 = 左腕偏转 (wrist yaw)
  右臂  Motor ID 21~27（对称同理）

MotorStatusMsg 字段（估计，以实际 SDK 定义为准）：
  name        : list[int]    电机 ID 列表
  pos         : list[float]  当前角度 (rad)
  spd         : list[float]  当前速度 (rad/s)
  tor         : list[float]  当前力矩 (Nm)
  temperature : list[float]  电机温度 (°C)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

try:
    import rclpy
    from rclpy.node import Node
    from bodyctrl_msgs.msg import MotorStatusMsg

    _ROS2_AVAILABLE = True
except ImportError:
    _ROS2_AVAILABLE = False

from src.robot_agent.bootstrap.logging import get_logger

logger = get_logger(__name__)

# ── 电机 ID 分组 ────────────────────────────────────────────────
LEFT_ARM_IDS  = list(range(11, 18))  # 11~17
RIGHT_ARM_IDS = list(range(21, 28))  # 21~27
ALL_ARM_IDS   = LEFT_ARM_IDS + RIGHT_ARM_IDS

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
    motor_ids:   list[int]   = field(default_factory=list)
    positions:   list[float] = field(default_factory=list)   # rad
    speeds:      list[float] = field(default_factory=list)   # rad/s
    torques:     list[float] = field(default_factory=list)   # Nm
    temperatures: list[float] = field(default_factory=list)  # °C
    timestamp:   float        = 0.0                           # time.monotonic()


@dataclass
class BothArmsState:
    """双臂完整快照。"""
    left:  ArmJointState = field(default_factory=ArmJointState)
    right: ArmJointState = field(default_factory=ArmJointState)
    is_valid: bool = False   # 是否已收到至少一帧真实数据


class ArmSubscriber:
    """
    机械臂状态订阅器（进程级单例）。

    在 ROS2 可用时后台 spin 订阅 /arm/status；
    在模拟模式下返回全零占位数据。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = BothArmsState()

        if not _ROS2_AVAILABLE:
            logger.warning("ArmSubscriber: rclpy/bodyctrl_msgs 不可用，进入模拟模式")
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

        # 后台线程持续 spin，不阻塞主线程
        self._spin_thread = threading.Thread(
            target=self._spin_loop, daemon=True, name="arm_status_spin"
        )
        self._spin_thread.start()
        logger.info("ArmSubscriber: 已启动后台订阅（/arm/status）")

    # ── 内部回调 ────────────────────────────────────────────────

    def _spin_loop(self) -> None:
        try:
            rclpy.spin(self._node)
        except Exception as exc:  # noqa: BLE001
            logger.error("ArmSubscriber: spin 异常退出", error=str(exc))

    def _on_status(self, msg: "MotorStatusMsg") -> None:  # type: ignore[name-defined]
        """将收到的 MotorStatusMsg 分左右臂存入缓存。"""
        # 构建 id -> index 映射
        id_map: dict[int, int] = {mid: i for i, mid in enumerate(msg.name)}

        def _extract(ids: list[int]) -> ArmJointState:
            s = ArmJointState()
            s.timestamp = time.monotonic()
            for mid in ids:
                idx = id_map.get(mid)
                if idx is None:
                    s.motor_ids.append(mid)
                    s.positions.append(0.0)
                    s.speeds.append(0.0)
                    s.torques.append(0.0)
                    s.temperatures.append(0.0)
                else:
                    s.motor_ids.append(mid)
                    s.positions.append(
                        msg.pos[idx] if idx < len(msg.pos) else 0.0
                    )
                    s.speeds.append(
                        msg.spd[idx] if idx < len(msg.spd) else 0.0
                    )
                    s.torques.append(
                        msg.tor[idx] if idx < len(msg.tor) else 0.0
                    )
                    s.temperatures.append(
                        msg.temperature[idx]
                        if hasattr(msg, "temperature") and idx < len(msg.temperature)
                        else 0.0
                    )
            return s

        left  = _extract(LEFT_ARM_IDS)
        right = _extract(RIGHT_ARM_IDS)

        with self._lock:
            self._state.left     = left
            self._state.right    = right
            self._state.is_valid = True

    # ── 公开方法 ────────────────────────────────────────────────

    def get_state(self) -> BothArmsState:
        """获取最新双臂状态快照（线程安全）。"""
        with self._lock:
            # 返回深拷贝，避免外部意外修改缓存
            import copy
            return copy.deepcopy(self._state)

    def get_formatted_status(self) -> str:
        """
        返回适合直接呈现给用户或 LLM 的可读状态字符串。

        示例输出：
          【左臂关节状态】
            J1 肩俯仰(shoulder_pitch)  : 位置=0.12 rad, 速度=0.00 rad/s, 力矩=1.23 Nm, 温度=32.1°C
            ...
          【右臂关节状态】
            ...
        """
        state = self.get_state()

        if self._sim_mode or not state.is_valid:
            stale_note = "（模拟模式，数据为占位值）" if self._sim_mode else "（尚未收到真实数据）"
            return (
                f"双臂状态暂不可用 {stale_note}。\n"
                "可先调用 reset_arms 工具让机械臂归零，再查询状态。"
            )

        # 检查数据新鲜度
        now = time.monotonic()
        age_left  = now - state.left.timestamp
        age_right = now - state.right.timestamp
        stale_warn = ""
        if age_left > 2.0 or age_right > 2.0:
            stale_warn = f"\n⚠️ 状态数据已有 {max(age_left, age_right):.1f} 秒未更新，可能不是最新。"

        def _arm_lines(arm: ArmJointState, side_name: str) -> str:
            lines = [f"【{side_name}关节状态】"]
            for i, (mid, label) in enumerate(zip(arm.motor_ids, JOINT_LABELS)):
                pos  = arm.positions[i]   if i < len(arm.positions)    else 0.0
                spd  = arm.speeds[i]      if i < len(arm.speeds)       else 0.0
                tor  = arm.torques[i]     if i < len(arm.torques)      else 0.0
                temp = arm.temperatures[i] if i < len(arm.temperatures) else 0.0
                lines.append(
                    f"  J{i+1} {label:<32s}: "
                    f"位置={pos:+.3f} rad, "
                    f"速度={spd:+.3f} rad/s, "
                    f"力矩={tor:+.2f} Nm, "
                    f"温度={temp:.1f}°C"
                )
            return "\n".join(lines)

        result = (
            _arm_lines(state.left,  "左臂")
            + "\n\n"
            + _arm_lines(state.right, "右臂")
            + stale_warn
        )
        return result

    def destroy(self) -> None:
        """释放 ROS2 节点资源。"""
        if not self._sim_mode:
            self._node.destroy_node()
