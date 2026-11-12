"""判定结束后回到本局开局姿态：生成平滑插值帧，复用原双臂播放器。"""

import math
import os

from script.game_trajectory import play_arm_frames


def build_return_frames(start: dict[int, float], target: dict[int, float],
                        min_duration: float, speed: float,
                        interval: float = 0.02) -> tuple[list[dict], float]:
    """五次插值缓起缓停；按最大关节位移延长时间，使理论峰值速度不超过上限。"""
    if any(not math.isfinite(v) or v <= 0 for v in (min_duration, speed, interval)):
        raise ValueError("回位时间、速度和采样周期必须为有限正数")
    if set(start) != set(target) or not start:
        raise ValueError("回位姿态的关节必须完整匹配")
    if any(not math.isfinite(v) for v in (*start.values(), *target.values())):
        raise ValueError("回位姿态包含无效关节角度")
    distance = max(abs(target[mid] - start[mid]) for mid in target)
    # smoothstep 五次曲线的最大导数为 1.875。
    steps = max(1, math.ceil(max(min_duration, 1.875 * distance / speed) / interval))
    frames = []
    for index in range(steps + 1):
        t = index / steps
        blend = t ** 3 * (10 - 15 * t + 6 * t * t)
        frames.append({"arms": {str(mid): start[mid] + (target[mid] - start[mid]) * blend
                                for mid in target}})
    # 末帧精确使用保存值，避免浮点插值给最终保持目标带来舍入偏差。
    frames[-1] = {"arms": {str(mid): value for mid, value in target.items()}}
    return frames, steps * interval


def return_to_initial_pose(node, initial_pose: dict[int, float]) -> None:
    """从当前实测位置直接缓慢回位，不读取或倒放录制轨迹。"""
    start = node.capture_arm_pose()
    duration = float(os.getenv("GAME_RETURN_DURATION_SEC", "4.0"))
    speed = float(os.getenv("GAME_RETURN_SPEED_RAD_SEC", "0.35"))
    frames, duration = build_return_frames(start, initial_pose, duration, speed)
    print(f"[LOG][GAME] 缓慢回到本局开局姿态，预计 {duration:.2f} 秒。")
    play_arm_frames(node, frames, 0.02, speed=speed)
