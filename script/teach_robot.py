#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ── ROS2 workspace Python 路径注入 ──────────
import glob as _glob
import os as _os
import sys as _sys
import json # 【新增】用于保存文件

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
record_timer = 100
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
        
        # 【新增】轨迹存储容器
        self.trajectory_data = [] 
        
        # 控制循环 (10Hz) - 统一管理全身发送
        self.ctrl_timer = self.create_timer(0.1, self.ctrl_cb)
        
        # 打印循环
        self.last_print_time = time.time()
        self.print_timer = self.create_timer(record_timer, self.print_cb)

        # 【新增】录制循环 (每0.1s记录一次limp状态)
        self.record_timer = self.create_timer(record_timer, self.record_cb)

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
                # 纯位置环保持。不断发送目标坐标，给一个常规的安全电流上限和速度上限
                item.pos = self.locked_arm_pos[mid]
                item.spd = 3.14  # 常规速度上限 (约半圈/秒)，防止瞬间抽动
                item.cur = 2.0   # 常规电流上限 (2.0A)，让电机自己计算需要的真实电流
                
            arm_msg.cmds.append(item)
        self.arm_cmd_pub.publish(arm_msg)

        # 2. 发送手部指令 (任何模式下都高频发布 target_hand_pos，彻底接管手部控制权)
        for hand_side, pub in [('left', self.lhand_cmd_pub), ('right', self.rhand_cmd_pub)]:
            h_msg = JointState()
            h_msg.header.stamp = self.get_clock().now().to_msg()
            h_msg.name = ['1', '2', '3', '4', '5', '6']
            h_msg.position = self.target_hand_pos[hand_side]
            pub.publish(h_msg)

    # 【新增】录制回调函数
    def record_cb(self):
        if self.mode == 'limp':
            frame = {
                "arms": self.current_arm_pos.copy(),
                "lhand": self.current_hand_pos['left'].copy(),
                "rhand": self.current_hand_pos['right'].copy()
            }
            self.trajectory_data.append(frame)

    def print_cb(self):
        now = time.time()
        if self.mode == 'limp' and (now - self.last_print_time >= 2.0):
            # 顺便在打印时提示录制了多少帧
            # self.do_print_status(f"👁️ [松弛监测 - 每2秒刷新] (后台已录制: {len(self.trajectory_data)} 帧)")
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
        # print(f"🔹 【左臂】 角度[deg]: {l_deg}")
        # print(f"🔸 【右臂】 角度[deg]: {r_deg}")
        # print(f"🖐️ 【左手】 比例(0-1): {lh_ratio}")
        # print(f"🖐️ 【右手】 比例(0-1): {rh_ratio}")
        # print(f"{'-'*55}")
        print("> ", end="", flush=True) # 保持输入提示符

def main():
    rclpy.init()
    node = FullBodyNode()
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()

    print("\n" + "★"*60)
    print(f" 🤖 天轶 2.0 Pro 【全身上半身示教 + 后台{record_timer}s录制】")
    print("★"*60)
    print("指令说明:")
    print(f"  [limp]  - 双臂松弛 (可拖动，且后台开始 {record_timer}s/帧录制)")
    print("  [lock]  - 双臂紧绷 (纯位置环保持锁定，暂停录制)")
    print("  [r 0 0 0 0 0 0] - 控制右手 (小|无名|中|食|拇弯|拇旋)")
    print("  [l 0 0 0 0 0 0] - 控制左手 (0=紧握, 100=全开)")
    print("  [b 0 0 0 0 0 0] - 同时控制双手")
    print("  [save]  - 保存轨迹数据到文件")
    print("  [clear] - 清空已录制的轨迹数据")
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
                print(f"✅ [双臂] 已进入松弛拖动模式 (后台正以{record_timer}s/帧录制中...)")
            elif cmd == 'lock':
                node.set_lock()
                print("🔒 [双臂] 已进入位置环锁定状态 (暂停录制)")
            # 【新增】保存功能
            elif cmd == 'save':
                save_path = _os.path.join(_os.getcwd(), 'path','trajectory.json')
                try:
                    with open(save_path, 'w', encoding='utf-8') as f:
                        json.dump(node.trajectory_data, f, indent=2)
                    print(f"💾 [成功] 动作轨迹已保存至: {save_path} (共 {len(node.trajectory_data)} 帧)")
                except Exception as e:
                    print(f"❌ [失败] 无法保存轨迹文件: {e}")
            # 【新增】清空功能
            elif cmd == 'clear':
                node.trajectory_data.clear()
                print("🗑️ 内存中的录制轨迹已清空！")
            elif cmd in ['r', 'l', 'b']:
                # if node.mode == 'idle':
                #     node.set_limp()
                    
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