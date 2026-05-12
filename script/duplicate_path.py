#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ── ROS2 workspace Python 路径注入 ──────────
import glob as _glob
import os as _os
import sys as _sys
import json

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

# 读取的文件名称
TRAJECTORY_FILE = "trajectory.json"
record_timer = 0.1
class PlaybackNode(Node):
    def __init__(self, trajectory_data):
        super().__init__('trajectory_playback_node')
        
        self.arm_cmd_pub = self.create_publisher(CmdSetMotorPosition, '/arm/cmd_pos', 10)
        self.lhand_cmd_pub = self.create_publisher(JointState, '/inspire_hand/ctrl/left_hand', 10)
        self.rhand_cmd_pub = self.create_publisher(JointState, '/inspire_hand/ctrl/right_hand', 10)
        
        self.trajectory = trajectory_data
        self.total_frames = len(self.trajectory)
        self.current_frame = 0
        
        self.get_logger().info(f"▶️ 轨迹加载完毕，共 {self.total_frames} 帧，总时长 {self.total_frames * record_timer:.1f} 秒。")
        
        # 严格执行 0.1s 的定时器回放
        self.play_timer = self.create_timer(record_timer, self.play_frame_cb)

    def play_frame_cb(self):
        if self.current_frame >= self.total_frames:
            print("\n✅ 轨迹回放结束！")
            self.play_timer.cancel()
            rclpy.shutdown()
            return
            
        frame = self.trajectory[self.current_frame]
        
        # 1. 组装并下发双臂指令 (严格复用你写的位置环参数)
        arm_msg = CmdSetMotorPosition()
        arm_msg.header.stamp = self.get_clock().now().to_msg()
        arm_msg.header.frame_id = 'playback_mode'
        arm_msg.cmds = []
        
        for mid_str, pos in frame["arms"].items():
            item = SetMotorPosition()
            item.name = int(mid_str)
            item.pos = float(pos)
            item.spd = 3.14  # 速度限制，保持动作平稳
            item.cur = 2.0   # 安全电流上限
            arm_msg.cmds.append(item)
            
        self.arm_cmd_pub.publish(arm_msg)

        # 2. 组装并下发双手指令
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