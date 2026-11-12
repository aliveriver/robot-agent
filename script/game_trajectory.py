"""猜拳轨迹适配：从 SQLite 加载帧，复用 duplicate_path.PlaybackNode。

load_game_trajectory() 只读数据库并转换帧格式；replay_game_trajectory()
同时驱动游戏和回放节点，保持猜拳手势，并在退出时接续最后的手臂目标。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path


def load_game_trajectory(db_path: Path, trajectory_id: int) -> tuple[list[dict], float, str]:
    """只读指定记录，不存在时抛错，避免误播另一条轨迹。"""
    uri = db_path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        row = connection.execute(
            "SELECT name, sample_interval_ms FROM trajectories WHERE id = ?",
            (trajectory_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"数据库中没有 ID={trajectory_id} 的轨迹: {db_path}")
        name, interval_ms = row
        if interval_ms <= 0:
            raise ValueError(f"轨迹 {name} 的采样间隔无效: {interval_ms}")
        rows = connection.execute(
            "SELECT left_arm, right_arm, left_hand, right_hand "
            "FROM trajectory_frames WHERE trajectory_id = ? ORDER BY frame_index",
            (trajectory_id,),
        ).fetchall()

    if not rows:
        raise ValueError(f"轨迹 {name} 没有帧数据")
    frames = []
    for values in rows:
        left_arm, right_arm, left_hand, right_hand = map(json.loads, values)
        if tuple(map(len, (left_arm, right_arm, left_hand, right_hand))) != (7, 7, 6, 6):
            raise ValueError(f"轨迹 {name} 的关节数量无效")
        arm_ids = list(range(11, 18)) + list(range(21, 28))
        frames.append({
            "arms": {str(mid): float(pos) for mid, pos in zip(arm_ids, left_arm + right_arm)},
            "lhand": left_hand,
            "rhand": right_hand,
        })
    return frames, interval_ms / 1000.0, name


def replay_game_trajectory(node, db_path: Path, trajectory_id: int) -> None:
    """复用原播放器，回放双臂时继续刷新游戏手势；失败向调用方抛错。"""
    import rclpy
    from script.duplicate_path import PlaybackNode

    frames, interval_sec, name = load_game_trajectory(db_path, trajectory_id)
    previous_modes = node.arm_mode.copy()
    playback_node = PlaybackNode(
        frames,
        interval_sec=interval_sec,
        publish_hands=False,
        shutdown_on_complete=False,
    )
    try:
        # 轨迹控制双臂期间暂停原锁臂命令，双手仍由游戏节点持续发布。
        node.set_arm_mode('b', 'idle')
        node.ctrl_cb()
        print(f"[LOG][GAME] 回放轨迹 ID={trajectory_id}「{name}」 ({len(frames)} 帧)")
        while rclpy.ok() and not playback_node.finished:
            rclpy.spin_once(node, timeout_sec=0.0)
            rclpy.spin_once(playback_node, timeout_sec=0.01)
        if not playback_node.finished:
            raise RuntimeError("ROS2 已退出，轨迹回放中断")
    finally:
        # 最后一个已发布目标用于接续锁臂，避免重新拉回开局姿势。
        node.current_arm_pos.update(playback_node.last_arm_positions)
        playback_node.destroy_node()
        for side, mode in previous_modes.items():
            node.set_arm_mode(side, mode)
