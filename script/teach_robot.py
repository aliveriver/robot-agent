#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ── ROS2 workspace Python 路径注入 ──────────
import glob as _glob
import os as _os
import sys as _sys

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

# 左右臂电机 ID
LEFT_ARM_IDS = [11, 12, 13, 14, 15, 16, 17]
RIGHT_ARM_IDS = [21, 22, 23, 24, 25, 26, 27]
ALL_ARM_IDS = LEFT_ARM_IDS + RIGHT_ARM_IDS

class FullBodyNode(Node):
    def __init__(self):
        super().__init__('full_body_teach_node')
        
        # 1. 订阅状态
        self.arm_status_sub = self.create_subscription(MotorStatusMsg, '/arm/status', self.arm_status_cb, 10)
        self.lhand_status_sub = self.create_subscription(JointState, '/inspire_hand/state/left_hand', self.lhand_status_cb, 10)
        self.rhand_status_sub = self.create_subscription(JointState, '/inspire_hand/state/right_hand', self.rhand_status_cb, 10)
        
        # 2. 发布命令
        self.arm_cmd_pub = self.create_publisher(CmdSetMotorPosition, '/arm/cmd_pos', 10)
        self.lhand_cmd_pub = self.create_publisher(JointState, '/inspire_hand/ctrl/left_hand', 10)
        self.rhand_cmd_pub = self.create_publisher(JointState, '/inspire_hand/ctrl/right_hand', 10)
        
        # 数据存储
        self.current_arm_pos = {mid: 0.0 for mid in ALL_ARM_IDS}
        self.current_hand_pos = {'left': [1.0]*6, 'right': [1.0]*6}
        self.locked_arm_pos = {mid: 0.0 for mid in ALL_ARM_IDS}
        
        # 目标手部位置 (由用户命令更新，后台定时器负责高频发布)
        self.target_hand_pos = {'left': [1.0]*6, 'right': [1.0]*6}
        self.hands_initialized = False
        
        self.mode = 'idle' # 'idle', 'limp', 'lock'
        
        # 控制循环 (10Hz) - 统一管理全身发送
        self.ctrl_timer = self.create_timer(0.1, self.ctrl_cb)
        
        # 打印循环
        self.last_print_time = time.time()
        self.print_timer = self.create_timer(0.5, self.print_cb)

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

    def set_hand_target(self, target_hand, values):
        """处理用户输入的手部角度并更新目标值"""
        ratios = []
        for v in values:
            ratio = (v / 100.0) if v > 1.5 else v
            ratios.append(float(max(0.0, min(1.0, ratio))))
            
        if target_hand in ['l', 'b']:
            self.target_hand_pos['left'] = ratios.copy()
        if target_hand in ['r', 'b']:
            self.target_hand_pos['right'] = ratios.copy()

    def set_limp(self):
        # 第一次激活控制时，同步手部当前状态，防止乱动
        if not self.hands_initialized:
            self.target_hand_pos['left'] = self.current_hand_pos['left'].copy()
            self.target_hand_pos['right'] = self.current_hand_pos['right'].copy()
            self.hands_initialized = True
            
        self.mode = 'limp'
        self.last_print_time = time.time()

    def set_lock(self):
        self.locked_arm_pos = self.current_arm_pos.copy()
        self.mode = 'lock'
        self.last_print_time = time.time()

    def ctrl_cb(self):
        if self.mode == 'idle':
            return
            
        # 1. 发送手臂指令
        arm_msg = CmdSetMotorPosition()
        arm_msg.header.stamp = self.get_clock().now().to_msg()
        arm_msg.header.frame_id = 'teach_mode'
        arm_msg.cmds = []

        for mid in ALL_ARM_IDS:
            item = SetMotorPosition()
            item.name = mid
            if self.mode == 'limp':
                item.pos = self.current_arm_pos[mid]
                item.spd = 0.0
                item.cur = 0.0 
            elif self.mode == 'lock':
                item.pos = self.locked_arm_pos[mid]
                item.spd = 10.0
                
                # 针对大关节（肩膀、大臂）给予更大的保持电流
                if mid in [11, 12, 21, 22]:
                    item.cur = 3.0  # 给大关节 4.0A 的力量对抗重力
                else:
                    item.cur = 1.5  # 小关节保持 1.5A 足矣
            arm_msg.cmds.append(item)
        self.arm_cmd_pub.publish(arm_msg)

        # 2. 发送手部指令 (任何模式下都高频发布 target_hand_pos，彻底接管手部控制权)
        for hand_side, pub in [('left', self.lhand_cmd_pub), ('right', self.rhand_cmd_pub)]:
            h_msg = JointState()
            h_msg.header.stamp = self.get_clock().now().to_msg()
            h_msg.name = ['1', '2', '3', '4', '5', '6']
            h_msg.position = self.target_hand_pos[hand_side]
            pub.publish(h_msg)

    def print_cb(self):
        now = time.time()
        if self.mode == 'limp' and (now - self.last_print_time >= 20.0):
            self.do_print_status("👁️ [松弛监测 - 每2秒刷新]")
            self.last_print_time = now
        elif self.mode == 'lock' and (now - self.last_print_time >= 30.0):
            self.do_print_status("💤 [紧绷保持 - 每30秒心跳]")
            self.last_print_time = now

    def do_print_status(self, header_text):
        print(f"\n{'-'*55}")
        print(header_text)
        l_deg = [round(math.degrees(self.current_arm_pos[mid]), 2) for mid in LEFT_ARM_IDS]
        r_deg = [round(math.degrees(self.current_arm_pos[mid]), 2) for mid in RIGHT_ARM_IDS]
        lh_ratio = [round(v, 3) for v in self.current_hand_pos['left']]
        rh_ratio = [round(v, 3) for v in self.current_hand_pos['right']]
        print(f"🔹 【左臂】 角度[deg]: {l_deg}")
        print(f"🔸 【右臂】 角度[deg]: {r_deg}")
        print(f"🖐️ 【左手】 比例(0-1): {lh_ratio}")
        print(f"🖐️ 【右手】 比例(0-1): {rh_ratio}")
        print(f"{'-'*55}")
        print("> ", end="", flush=True) # 保持输入提示符

def main():
    rclpy.init()
    node = FullBodyNode()
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()

    print("\n" + "★"*60)
    print(" 🤖 天轶 2.0 Pro 【全身上半身示教 + 手指调参控制台】")
    print("★"*60)
    print("指令说明:")
    print("  [limp]  - 双臂松弛 (可拖动)")
    print("  [lock]  - 双臂紧绷 (锁死当前姿态)")
    print("  [r 0 0 0 0 0 0] - 控制右手 (小|无名|中|食|拇弯|拇旋)")
    print("  [l 0 0 0 0 0 0] - 控制左手 (0=紧握, 100=全开)")
    print("  [b 0 0 0 0 0 0] - 同时控制双手")
    print("  [q] - 退出程序")
    print("="*60)
    
    try:
        while True:
            cmd_line = input("> ").strip().lower()
            if not cmd_line:
                continue
                
            parts = cmd_line.split()
            cmd = parts[0]
            
            if cmd == 'q':
                break
            elif cmd == 'limp':
                node.set_limp()
                print("✅ [双臂] 已进入松弛拖动模式 (每2秒打印数据)")
            elif cmd == 'lock':
                node.set_lock()
                print("🔒 [双臂] 已紧绷锁死 (每30秒打印心跳)")
            elif cmd in ['r', 'l', 'b']:
                if node.mode == 'idle':
                    # 如果还没激活模式，自动帮用户切到 limp 激活后台发送
                    node.set_limp()
                    
                if len(parts) == 7:
                    try:
                        values = [float(x) for x in parts[1:]]
                        node.set_hand_target(cmd, values)
                        hand_name = {"r": "右手", "l": "左手", "b": "双手"}[cmd]
                        print(f"👉 [{hand_name}] 指令已接收，后台正持续下发...")
                    except ValueError:
                        print("❌ 参数错误: 必须是数字！")
                else:
                    print(f"❌ 格式错误: 必须跟 6 个数值。示例: {cmd} 0 0 100 100 100 100")
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