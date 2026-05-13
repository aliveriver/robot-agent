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
import math
import threading
import rclpy
from rclpy.node import Node

try:
    from bodyctrl_msgs.msg import MotorStatusMsg, CmdSetMotorPosition, SetMotorPosition
    from sensor_msgs.msg import JointState
except ImportError as e:
    print(f"\n[FAIL] 核心消息包不可导入: {e}")
    _sys.exit(1)

LEFT_ARM_IDS = [11, 12, 13, 14, 15, 16, 17]
RIGHT_ARM_IDS = [21, 22, 23, 24, 25, 26, 27]
ALL_ARM_IDS = LEFT_ARM_IDS + RIGHT_ARM_IDS
RECORD_INTERVAL = 0.1  # 修复了原代码中100秒的bug，改为0.1秒

class TrajectoryRecorderNode(Node):
    def __init__(self):
        super().__init__('trajectory_recorder_node')
        
        # 订阅状态
        self.arm_status_sub = self.create_subscription(MotorStatusMsg, '/arm/status', self.arm_status_cb, 10)
        self.lhand_status_sub = self.create_subscription(JointState, '/inspire_hand/state/left_hand', self.lhand_status_cb, 10)
        self.rhand_status_sub = self.create_subscription(JointState, '/inspire_hand/state/right_hand', self.rhand_status_cb, 10)
        
        # 发布命令 (仅用于发送松弛指令)
        self.arm_cmd_pub = self.create_publisher(CmdSetMotorPosition, '/arm/cmd_pos', 10)
        
        # 数据存储
        self.current_arm_pos = {mid: 0.0 for mid in ALL_ARM_IDS}
        self.current_hand_pos = {'left': [1.0]*6, 'right': [1.0]*6}
        self.trajectory_data = [] 
        
        self.is_recording = False
        
        # 控制循环 (保持松弛状态)
        self.ctrl_timer = self.create_timer(0.1, self.ctrl_cb)
        # 录制循环
        self.record_timer = self.create_timer(RECORD_INTERVAL, self.record_cb)
        self.last_print_time = time.time()

    def arm_status_cb(self, msg):
        for item in msg.status:
            if int(item.name) in ALL_ARM_IDS:
                self.current_arm_pos[int(item.name)] = float(item.pos)

    def lhand_status_cb(self, msg):
        if len(msg.position) >= 6:
            self.current_hand_pos['left'] = list(msg.position[:6])

    def rhand_status_cb(self, msg):
        if len(msg.position) >= 6:
            self.current_hand_pos['right'] = list(msg.position[:6])

    def ctrl_cb(self):
        if not self.is_recording:
            return
            
        # 持续发送松弛指令 (电流限制为0)
        arm_msg = CmdSetMotorPosition()
        arm_msg.header.stamp = self.get_clock().now().to_msg()
        arm_msg.header.frame_id = 'teach_mode'
        arm_msg.cmds = []

        for mid in ALL_ARM_IDS:
            item = SetMotorPosition()
            item.name = mid
            item.pos = self.current_arm_pos[mid]
            item.spd = 0.0
            item.cur = 0.0 
            arm_msg.cmds.append(item)
            
        self.arm_cmd_pub.publish(arm_msg)

    def record_cb(self):
        if self.is_recording:
            frame = {
                "arms": self.current_arm_pos.copy(),
                "lhand": self.current_hand_pos['left'].copy(),
                "rhand": self.current_hand_pos['right'].copy()
            }
            self.trajectory_data.append(frame)
            
            now = time.time()
            if now - self.last_print_time >= 2.0:
                print(f"\r👁️ [录制中] 后台已录制: {len(self.trajectory_data)} 帧", end="", flush=True)
                self.last_print_time = now

def main():
    rclpy.init()
    node = TrajectoryRecorderNode()
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()

    print("\n" + "★"*60)
    print(" 🎥 轨迹录制专用工具 (默认松弛模式)")
    print("★"*60)
    print("指令说明:")
    print("  [start] - 开始/恢复录制 (自动进入双臂松弛状态)")
    print("  [pause] - 暂停录制")
    print("  [save]  - 保存轨迹数据到文件")
    print("  [clear] - 清空已录制的轨迹数据")
    print("  [q]     - 退出程序")
    print("="*60)
    
    try:
        while True:
            cmd_line = input("\n> ").strip().lower()
            if not cmd_line:
                continue
            
            if cmd_line == 'q':
                break
            elif cmd_line == 'start':
                node.is_recording = True
                print("✅ 录制已启动，双臂进入松弛拖动状态。")
            elif cmd_line == 'pause':
                node.is_recording = False
                print("⏸️ 录制已暂停。")
            elif cmd_line == 'save':
                save_dir = _os.path.join(_os.getcwd(), 'path')
                _os.makedirs(save_dir, exist_ok=True)
                save_path = _os.path.join(save_dir, 'trajectory.json')
                try:
                    with open(save_path, 'w', encoding='utf-8') as f:
                        json.dump(node.trajectory_data, f, indent=2)
                    print(f"💾 [成功] 动作轨迹已保存至: {save_path} (共 {len(node.trajectory_data)} 帧)")
                except Exception as e:
                    print(f"❌ [失败] 无法保存轨迹文件: {e}")
            elif cmd_line == 'clear':
                node.trajectory_data.clear()
                print("🗑️ 内存中的录制轨迹已清空！")
            else:
                print("❌ 未知指令！")
                
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[INFO] 正在关闭节点...")
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()