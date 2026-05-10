"""
tools/builtin/arm_tools.py - 机械臂与灵巧手控制工具

通过 LLM tool calling 接收从用户自然语言中提取的参数，
调用 ROS2 Publisher 接口控制机器人双臂双手。

工具列表：
  - move_arm_joints   控制单侧或双侧机械臂到指定关节角度
  - control_hand      控制灵巧手做预设手势或自定义角度
  - get_arm_status    读取当前双臂关节状态（文字描述）

依赖 ROS2 接口封装：
  interfaces/ros2/arm_publisher.py
  interfaces/ros2/hand_publisher.py
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState
from src.robot_agent.interfaces.ros2.arm_publisher import LEFT_ARM_IDS, RIGHT_ARM_IDS
from src.robot_agent.interfaces.ros2.arm_subscriber import JOINT_LABELS

logger = get_logger(__name__)

# ──────────────────────────────────────────────
# 懒加载 ROS2 接口单例（避免非 ROS2 环境 import 失败）
# ──────────────────────────────────────────────
_arm_pub = None
_arm_sub = None
_hand_pub = None


def _get_arm_pub():
    global _arm_pub
    if _arm_pub is None:
        from src.robot_agent.interfaces.ros2.arm_publisher import ArmPublisher
        _arm_pub = ArmPublisher()
    return _arm_pub


def _get_arm_sub():
    global _arm_sub
    if _arm_sub is None:
        from src.robot_agent.interfaces.ros2.arm_subscriber import ArmSubscriber
        _arm_sub = ArmSubscriber()
    return _arm_sub


def _get_hand_pub():
    global _hand_pub
    if _hand_pub is None:
        from src.robot_agent.interfaces.ros2.hand_publisher import HandPublisher
        _hand_pub = HandPublisher()
    return _hand_pub


# ──────────────────────────────────────────────
# 预设手势库
# 值为 6 个手指归一化角度：[小指, 无名指, 中指, 食指, 拇指弯曲, 拇指旋转]
# 0.0 = 完全张开；1.0 = 完全合拢
# ──────────────────────────────────────────────
PRESET_GESTURES: dict[str, list[float]] = {
    "open":    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],   # 手掌展开
    "close":   [1.0, 1.0, 1.0, 1.0, 1.0, 0.5],   # 握拳
    "pinch":   [1.0, 1.0, 1.0, 0.8, 0.8, 0.3],   # 捏取（食指拇指对捏）
    "point":   [1.0, 1.0, 1.0, 0.0, 1.0, 0.3],   # 指向（食指伸直）
    "thumbup": [1.0, 1.0, 1.0, 1.0, 0.0, 0.0],   # 大拇指竖起
    "peace":   [1.0, 1.0, 0.0, 0.0, 1.0, 0.3],   # 比 V / 剪刀手
    "rock":    [1.0, 0.0, 0.0, 0.0, 0.0, 0.0],   # 摇滚（小指食指伸直）
    "ok":      [0.0, 0.0, 0.0, 0.8, 0.8, 0.3],   # OK 手势
}

# 中文手势别名映射
GESTURE_ALIASES: dict[str, str] = {
    "张开": "open",
    "展开": "open",
    "伸开": "open",
    "握拳": "close",
    "合拢": "close",
    "捏": "pinch",
    "捏取": "pinch",
    "指向": "point",
    "指": "point",
    "竖大拇指": "thumbup",
    "赞": "thumbup",
    "剪刀手": "peace",
    "V": "peace",
    "OK": "ok",
    "摇滚": "rock",
}

# 手臂中文映射
SIDE_MAP_CN: dict[str, str] = {
    "左": "left",
    "右": "right",
    "双": "both",
    "两": "both",
}

SIDE_DISPLAY_CN: dict[str, str] = {
    "left": "左臂",
    "right": "右臂",
    "both": "双臂",
}

HAND_DISPLAY_CN: dict[str, str] = {
    "left": "左手",
    "right": "右手",
}


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

async def move_arm_joints(
    state: AgentState,
    side: str = "left",
    positions: list[float] | None = None,
    kp: float = 100.0,
    kd: float = 2.0,
    **kwargs: Any,
) -> dict:
    """
    控制机器人左臂、右臂或双臂移动到指定关节角度。

    《关节布局》（每侧 7 个关节，索引 0~6）：
      J1 肩俧仰 shoulder_pitch  范围：-1.57 ~ +1.57 rad
      J2 肩侧摇 shoulder_roll   范围：-1.57 ~ +1.57 rad
      J3 肩旋转 shoulder_yaw    范围：-1.57 ~ +1.57 rad
      J4 肘弯曲 elbow           范围：  0.00 ~ +2.36 rad
      J5 腕旋转 wrist_roll      范围：-1.57 ~ +1.57 rad
      J6 腕俧仰 wrist_pitch     范围：-1.04 ~ +1.04 rad
      J7 腕偏转 wrist_yaw       范围：-0.79 ~ +0.79 rad

    《常用参考姿态》（positions 实例）：
      自然垂侧（展开状态）: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
      居中展开（平降 90°）: [1.57, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
      前推平举（指向正前）: [0.0, -1.57, 0.0, 0.0, 0.0, 0.0, 0.0]
      居中居山房封弹（常用起始姿）: [0.3, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]

    《建议工作流程》：
      1. 先调用 get_arm_status 获取当前关节位置（避免盲目大幅度跃变）
      2. 基于当前状态小幅度调整，单次调整建议不超过 0.5 rad
      3. 需要大范围运动时，分多步调用本工具

    Args:
        side:      "left" | "right" | "both"
        positions: 7 个关节目标角度（弧度），按 J1~J7 顺序提供
        kp:        位置增益（默认 100.0，慢速兴起可调小至 50.0）
        kd:        阻尼增益（默认 2.0）
    """
    if positions is None or len(positions) != 7:
        err = f"positions 必须是包含 7 个弧度值的列表，当前收到：{positions}"
        logger.warning("move_arm_joints: 参数错误", error=err)
        return {
            "ok": False,
            "error": err,
            "state_updates": {
                "response_text": "手臂控制失败：需要提供 7 个关节角度值（弧度）。"
                if state.language == "cn"
                else "Arm control failed: 7 joint positions (rad) required."
            },
        }

    side = side.lower()
    if side not in ("left", "right", "both"):
        side = "left"

    def _send() -> None:
        pub = _get_arm_pub()
        if side in ("left", "both"):
            pub.send_position(LEFT_ARM_IDS, positions, speed_rpm=10.0, current_a=1.0)
        if side in ("right", "both"):
            pub.send_position(RIGHT_ARM_IDS, positions, speed_rpm=10.0, current_a=1.0)

    await asyncio.to_thread(_send)

    display = SIDE_DISPLAY_CN.get(side, side)
    message = (
        f"{display}已移动到目标姿态。"
        if state.language == "cn"
        else f"The {side} arm has moved to the target pose."
    )
    logger.info("move_arm_joints: 指令已发送", side=side, positions=positions)
    return {
        "ok": True,
        "message": message,
        "side": side,
        "positions": positions,
        "state_updates": {"response_text": message},
    }


async def control_hand(
    state: AgentState,
    side: str = "left",
    gesture: str = "open",
    angles: list[float] | None = None,
    **kwargs: Any,
) -> dict:
    """
    控制灵巧手做预设手势或自定义角度。

    Args:
        side:    "left" | "right"
        gesture: 预设名称（open/close/pinch/point/thumbup/peace/rock/ok）
                 或 "custom" 配合 angles 参数
        angles:  gesture="custom" 时使用，6 个手指角度 0.0~1.0
    """
    side = side.lower()
    if side not in ("left", "right"):
        side = "left"

    # 解析手势（兼容中文别名）
    gesture_key = GESTURE_ALIASES.get(gesture, gesture.lower())

    if gesture_key == "custom":
        if angles is None or len(angles) != 6:
            err = "custom 模式需要提供包含 6 个值的 angles 列表（0.0~1.0）"
            return {
                "ok": False,
                "error": err,
                "state_updates": {"response_text": "手势控制失败：自定义模式需要 6 个手指角度值。"},
            }
        target_angles = [max(0.0, min(1.0, a)) for a in angles]
    else:
        target_angles = PRESET_GESTURES.get(gesture_key)
        if target_angles is None:
            available = "、".join(PRESET_GESTURES.keys())
            err = f"未知手势 '{gesture}'，可用手势：{available}"
            return {
                "ok": False,
                "error": err,
                "state_updates": {
                    "response_text": f"不支持手势'{gesture}'，支持的手势有：{available}。"
                },
            }

    def _send() -> None:
        pub = _get_hand_pub()
        pub.set_hand_angles(side, target_angles)

    await asyncio.to_thread(_send)

    hand_display = HAND_DISPLAY_CN.get(side, side)
    gesture_display = gesture_key
    message = (
        f"{hand_display}已完成'{gesture_display}'手势。"
        if state.language == "cn"
        else f"The {side} hand performed the '{gesture_display}' gesture."
    )
    logger.info("control_hand: 指令已发送", side=side, gesture=gesture_key, angles=target_angles)
    return {
        "ok": True,
        "message": message,
        "side": side,
        "gesture": gesture_key,
        "angles": target_angles,
        "state_updates": {"response_text": message},
    }


async def control_both_hands(
    state: AgentState,
    left_gesture: str = "open",
    right_gesture: str = "open",
    left_angles: list[float] | None = None,
    right_angles: list[float] | None = None,
    **kwargs: Any,
) -> dict:
    """
    同时控制左右双手做（可以不同的）手势。

    Args:
        left_gesture:  左手手势名称
        right_gesture: 右手手势名称
        left_angles:   左手自定义角度（left_gesture="custom" 时使用）
        right_angles:  右手自定义角度（right_gesture="custom" 时使用）
    """
    # 分别解析左右手手势
    def _resolve(gesture: str, angles: list[float] | None) -> list[float] | str:
        key = GESTURE_ALIASES.get(gesture, gesture.lower())
        if key == "custom":
            if angles and len(angles) == 6:
                return [max(0.0, min(1.0, a)) for a in angles]
            return "角度参数错误"
        resolved = PRESET_GESTURES.get(key)
        if resolved is None:
            return f"未知手势 '{gesture}'"
        return resolved

    left_target  = _resolve(left_gesture, left_angles)
    right_target = _resolve(right_gesture, right_angles)

    if isinstance(left_target, str):
        return {"ok": False, "error": f"左手：{left_target}"}
    if isinstance(right_target, str):
        return {"ok": False, "error": f"右手：{right_target}"}

    def _send() -> None:
        pub = _get_hand_pub()
        pub.set_both_hands(left_target, right_target)

    await asyncio.to_thread(_send)

    message = (
        f"左手完成'{left_gesture}'手势，右手完成'{right_gesture}'手势。"
        if state.language == "cn"
        else f"Left hand: '{left_gesture}', right hand: '{right_gesture}'."
    )
    logger.info(
        "control_both_hands: 指令已发送",
        left_gesture=left_gesture,
        right_gesture=right_gesture,
    )
    return {
        "ok": True,
        "message": message,
        "state_updates": {"response_text": message},
    }


async def reset_arms(
    state: AgentState,
    **kwargs: Any,
) -> dict:
    """
    双臂关节清零（回零位）。
    """
    def _send() -> None:
        pub = _get_arm_pub()
        pub.set_zero("all")

    await asyncio.to_thread(_send)

    message = (
        "双臂已回到零位。"
        if state.language == "cn"
        else "Both arms have been reset to zero position."
    )
    logger.info("reset_arms: 关节清零指令已发送")
    return {"ok": True, "message": message, "state_updates": {"response_text": message}}


async def list_gestures(
    state: AgentState,
    **kwargs: Any,
) -> dict:
    """
    列出当前支持的所有预设手势名称。
    """
    names = list(PRESET_GESTURES.keys())
    names_str = "、".join(names)
    message = (
        f"当前支持的手势有：{names_str}。"
        if state.language == "cn"
        else f"Available gestures: {', '.join(names)}."
    )
    return {"ok": True, "gestures": names, "state_updates": {"response_text": message}}


async def get_arm_status(
    state: AgentState,
    **kwargs: Any,
) -> dict:
    """
    读取并返回当前双臂关节的实时状态。

    返回每个关节的：位置（rad）、速度（rad/s）、力矩（Nm）、温度（°C）。
    建议在发送运动指令前先调用本工具，了解当前姿态。
    """
    def _read() -> str:
        sub = _get_arm_sub()
        return sub.get_formatted_status()

    status_text = await asyncio.to_thread(_read)

    # [ARM_STATUS_UNAVAILABLE] 标记表示驱动未就绪或数据尚未到达
    is_unavailable = status_text.startswith("[ARM_STATUS_UNAVAILABLE]")

    if is_unavailable:
        # response_text：播报给用户，不暴露技术细节
        user_reply = (
            "当前无法读取机械臂状态，驱动程序可能尚未启动，请稍后再试。"
            if state.language == "cn"
            else "Unable to read arm status. The driver may not be running yet."
        )
    else:
        # 正常：语音只做简短确认，完整数据在 status 字段供 LLM 规划运动
        user_reply = (
            "已读取到双臂关节状态。"
            if state.language == "cn"
            else "Arm status retrieved."
        )

    logger.info("get_arm_status: 状态已读取", unavailable=is_unavailable)
    return {
        "ok": not is_unavailable,
        # status：完整技术数据，LLM 依此决定运动目标角度
        "status": status_text,
        # response_text：用户听到的简短语音
        "state_updates": {"response_text": user_reply},
    }
