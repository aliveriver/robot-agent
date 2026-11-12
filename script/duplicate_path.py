#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件轨迹回放入口及可复用 PlaybackNode；猜拳可关闭手指回放和全局退出。"""

# ── ROS2 workspace Python 路径注入 ──────────
import glob as _glob
import os as _os
import sys as _sys
import json
import math

_ROS2_WS = _os.environ.get("ROS2_WS", "/opt/PARTITIONS/A/ros2ws")
_injected = []
for _p in _glob.glob(f"{_ROS2_WS}/install/*/local/lib/python*/dist-packages"):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
        _injected.append(_p)
# ──────────────────────────────────────────

import time
import rclpy
from rclpy.node import Node

from bodyctrl_msgs.msg import CmdSetMotorPosition, SetMotorPosition
from sensor_msgs.msg import JointState

if __package__:
    from .trajectory_manager.arm_currents import ARM_CURRENTS
else:
    from trajectory_manager.arm_currents import ARM_CURRENTS

# 读取的文件名称
TRAJECTORY_FILE = "trajectory.json"
record_timer = 0.1
class PlaybackNode(Node):
    """按指定采样周期发布轨迹，独立运行时沿用原双臂、双手回放行为。"""

    def __init__(
        self,
        trajectory_data,
        *,
        interval_sec=None,
        publish_hands=True,
        shutdown_on_complete=True,
        speed=3.14,
    ):
        super().__init__('trajectory_playback_node')
        
        self.arm_cmd_pub = self.create_publisher(CmdSetMotorPosition, '/arm/cmd_pos', 10)
        self.lhand_cmd_pub = self.create_publisher(JointState, '/inspire_hand/ctrl/left_hand', 10)
        self.rhand_cmd_pub = self.create_publisher(JointState, '/inspire_hand/ctrl/right_hand', 10)
        
        self.trajectory = trajectory_data
        self.total_frames = len(self.trajectory)
        self.current_frame = 0
        self.interval_sec = float(record_timer if interval_sec is None else interval_sec)
        if self.interval_sec <= 0:
            raise ValueError("回放采样间隔必须大于零")
        self.publish_hands = publish_hands
        self.shutdown_on_complete = shutdown_on_complete
        self.speed = float(speed)
        if not math.isfinite(self.speed) or self.speed <= 0:
            raise ValueError("关节速度上限必须为有限正数")
        self.finished = False
        self.last_arm_positions = {}
        
        self.get_logger().info(f"▶️ 轨迹加载完毕，共 {self.total_frames} 帧，总时长 {self.total_frames * self.interval_sec:.1f} 秒。")
        
        # 支持旧式固定周期文件轨迹和数据库保存的不同采样周期。
        self.play_timer = self.create_timer(self.interval_sec, self.play_frame_cb)

    def play_frame_cb(self):
        if self.current_frame >= self.total_frames:
            if self.finished:
                return
            print("\n✅ 轨迹回放结束！")
            self.play_timer.cancel()
            self.finished = True
            if self.shutdown_on_complete:
                rclpy.shutdown()
            return
            
        frame = self.trajectory[self.current_frame]
        
        # 1. 组装并下发双臂指令，与猜拳保持姿势共用逐关节电流配置。
        arm_msg = CmdSetMotorPosition()
        arm_msg.header.stamp = self.get_clock().now().to_msg()
        arm_msg.header.frame_id = 'playback_mode'
        arm_msg.cmds = []
        
        for mid_str, pos in frame["arms"].items():
            item = SetMotorPosition()
            item.name = int(mid_str)
            item.pos = float(pos)
            item.spd = self.speed
            item.cur = ARM_CURRENTS[item.name]
            arm_msg.cmds.append(item)
            
        self.arm_cmd_pub.publish(arm_msg)
        self.last_arm_positions = {
            int(mid_str): float(pos) for mid_str, pos in frame["arms"].items()
        }

        # 2. 组装并下发双手指令
        if self.publish_hands:
            for side, pub, key in [('left', self.lhand_cmd_pub, 'lhand'), ('right', self.rhand_cmd_pub, 'rhand')]:
                h_msg = JointState()
                h_msg.header.stamp = self.get_clock().now().to_msg()
                h_msg.name = ['1', '2', '3', '4', '5', '6']
                h_msg.position = frame[key]
                pub.publish(h_msg)

        print(f"\r⏳ 回放进度: [{self.current_frame + 1} / {self.total_frames}]", end="", flush=True)
        self.current_frame += 1

def main():
    print("="*55)
    print(" 🤖 机器人轨迹复现系统")
    print("="*55)
    
    # 1. 加载文件
    current_dir = _os.getcwd()
    filepath = _os.path.join(current_dir, 'path',TRAJECTORY_FILE)
    
    if not _os.path.exists(filepath):
        print(f"❌ 找不到文件: {filepath}\n请先在示教脚本中使用 'save' 指令生成！")
        _sys.exit(1)
        
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            trajectory_data = json.load(f)
    except Exception as e:
        print(f"❌ 解析 JSON 失败: {e}")
        _sys.exit(1)

    print(f"✅ 成功读取轨迹文件，包含 {len(trajectory_data)} 个关键帧。")
    input("▶️  请注意安全！按 [回车键] 立即开始复现动作...")

    # 2. 启动播放
    rclpy.init()
    node = PlaybackNode(trajectory_data)
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        print("\n⏹️ 已强制终止回放。")
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()
