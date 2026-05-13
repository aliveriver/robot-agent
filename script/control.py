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

LEFT_ARM_IDS = [11, 12, 13, 14, 15, 16, 17]
RIGHT_ARM_IDS = [21, 22, 23, 24, 25, 26, 27]

# 【新增】针对不同关节定义安全锁紧电流上限 (单位: A)
# 肩膀承受重力最大，适当调大；腕关节较小，保持2.0A
SAFE_LOCK_CURRENT = {
    # 左臂
    11: 6.0,  # 左肩俯仰 (峰值~28A)
    12: 5.0,  # 左肩翻滚 (峰值~17A)
    13: 4.0,  # 左肩偏航 (峰值~12A)
    14: 4.0,  # 左肘俯仰 (峰值~12A)
    15: 2.0,  # 左腕偏航
    16: 2.0,  # 左腕俯仰
    17: 2.0,  # 左腕翻滚
    # 右臂
    21: 6.0,  # 右肩俯仰
    22: 5.0,  # 右肩翻滚
    23: 4.0,  # 右肩偏航
    24: 4.0,  # 右肘俯仰
    25: 2.0,  # 右腕偏航
    26: 2.0,  # 右腕俯仰
    27: 2.0,  # 右腕翻滚
}

class ManipulatorControlNode(Node):
    def __init__(self):
        super().__init__('manipulator_control_node')
        
        # 订阅状态
        self.arm_status_sub = self.create_subscription(MotorStatusMsg, '/arm/status', self.arm_status_cb, 10)
        self.lhand_status_sub = self.create_subscription(JointState, '/inspire_hand/state/left_hand', self.lhand_status_cb, 10)
        self.rhand_status_sub = self.create_subscription(JointState, '/inspire_hand/state/right_hand', self.rhand_status_cb, 10)
        
        # 发布命令
        self.arm_cmd_pub = self.create_publisher(CmdSetMotorPosition, '/arm/cmd_pos', 10)
        self.lhand_cmd_pub = self.create_publisher(JointState, '/inspire_hand/ctrl/left_hand', 10)
        self.rhand_cmd_pub = self.create_publisher(JointState, '/inspire_hand/ctrl/right_hand', 10)
        
        # 状态数据
        self.current_arm_pos = {mid: 0.0 for mid in LEFT_ARM_IDS + RIGHT_ARM_IDS}
        self.current_hand_pos = {'left': [1.0]*6, 'right': [1.0]*6}
        
        # 各臂的锁定位置和当前模式 ('idle', 'limp', 'lock')
        self.locked_arm_pos = {mid: 0.0 for mid in LEFT_ARM_IDS + RIGHT_ARM_IDS}
        self.arm_mode = {'l': 'idle', 'r': 'idle'}
        
        # 手部目标
        self.target_hand_pos = {'left': [1.0]*6, 'right': [1.0]*6}
        
        # 控制循环 (10Hz) - 不断下发位置锁紧或松弛
        self.ctrl_timer = self.create_timer(0.1, self.ctrl_cb)

    def arm_status_cb(self, msg):
        for item in msg.status:
            mid = int(item.name)
            if mid in self.current_arm_pos:
                self.current_arm_pos[mid] = float(item.pos)

    def lhand_status_cb(self, msg):
        if len(msg.position) >= 6:
            self.current_hand_pos['left'] = list(msg.position[:6])

    def rhand_status_cb(self, msg):
        if len(msg.position) >= 6:
            self.current_hand_pos['right'] = list(msg.position[:6])

    def set_hand_target(self, target_hand, values):
        ratios = []
        for v in values:
            ratio = (v / 100.0) if v > 1.5 else v
            ratios.append(float(max(0.0, min(1.0, ratio))))
            
        if target_hand in ['l', 'b']:
            self.target_hand_pos['left'] = ratios.copy()
        if target_hand in ['r', 'b']:
            self.target_hand_pos['right'] = ratios.copy()

    def set_arm_mode(self, side, mode):
        targets = ['l', 'r'] if side == 'b' else [side]
        for t in targets:
            if mode == 'lock':
                # 记录当前位置作为锁定坐标
                ids = LEFT_ARM_IDS if t == 'l' else RIGHT_ARM_IDS
                for mid in ids:
                    self.locked_arm_pos[mid] = self.current_arm_pos[mid]
            self.arm_mode[t] = mode

    def ctrl_cb(self):
        # 1. 机械臂控制 (高频发送位置)
        arm_msg = CmdSetMotorPosition()
        arm_msg.header.stamp = self.get_clock().now().to_msg()
        arm_msg.header.frame_id = 'control_mode'
        arm_msg.cmds = []

        # 处理左臂
        if self.arm_mode['l'] != 'idle':
            for mid in LEFT_ARM_IDS:
                item = SetMotorPosition()
                item.name = mid
                if self.arm_mode['l'] == 'limp':
                    item.pos = self.current_arm_pos[mid]
                    item.spd = 0.0
                    item.cur = 0.0 
                elif self.arm_mode['l'] == 'lock':
                    item.pos = self.locked_arm_pos[mid]
                    item.spd = 3.14 
                    # 【修改】使用字典中定义的对应关节电流，如果未定义则默认2.0A
                    item.cur = SAFE_LOCK_CURRENT.get(mid, 2.0) 
                arm_msg.cmds.append(item)

        # 处理右臂
        if self.arm_mode['r'] != 'idle':
            for mid in RIGHT_ARM_IDS:
                item = SetMotorPosition()
                item.name = mid
                if self.arm_mode['r'] == 'limp':
                    item.pos = self.current_arm_pos[mid]
                    item.spd = 0.0
                    item.cur = 0.0 
                elif self.arm_mode['r'] == 'lock':
                    item.pos = self.locked_arm_pos[mid]
                    item.spd = 3.14 
                    # 【修改】使用字典中定义的对应关节电流，如果未定义则默认2.0A
                    item.cur = SAFE_LOCK_CURRENT.get(mid, 2.0) 
                arm_msg.cmds.append(item)

        if arm_msg.cmds:
            self.arm_cmd_pub.publish(arm_msg)

        # 2. 发送手部指令 (连续高频锁死手部比例)
        for hand_side, pub in [('left', self.lhand_cmd_pub), ('right', self.rhand_cmd_pub)]:
            h_msg = JointState()
            h_msg.header.stamp = self.get_clock().now().to_msg()
            h_msg.name = ['1', '2', '3', '4', '5', '6']
            h_msg.position = self.target_hand_pos[hand_side]
            pub.publish(h_msg)


def main():
    rclpy.init()
    node = ManipulatorControlNode()
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()

    print("\n" + "★"*60)
    print(" 🛠️ 机械臂与手部独立控制工具 (已强化肩部锁紧电流)")
    print("★"*60)
    print("指令说明:")
    print("  [limp l/r/b] - 对应左臂/右臂/双臂进入松弛拖动状态")
    print("  [lock l/r/b] - 对应左臂/右臂/双臂原地锁紧 (保持当前坐标)")
    print("  [r 0 0 0 0 0 0] - 控制右手 (小|无名|中|食|拇弯|拇旋, 0=紧握, 100=全开)")
    print("  [l 0 0 0 0 0 0] - 控制左手")
    print("  [b 0 0 0 0 0 0] - 同时控制双手")
    print("  [q] - 退出程序")
    print("="*60)
    
    try:
        while True:
            cmd_line = input("\n> ").strip().lower()
            if not cmd_line:
                continue
            parts = cmd_line.split()
            cmd = parts[0]
            
            if cmd == 'q':
                break
            
            # 手臂独立控制
            elif cmd in ['limp', 'lock']:
                if len(parts) == 2 and parts[1] in ['l', 'r', 'b']:
                    side = parts[1]
                    node.set_arm_mode(side, cmd)
                    side_name = {'l': '左臂', 'r': '右臂', 'b': '双臂'}[side]
                    action = "松弛 (0电流)" if cmd == 'limp' else "锁紧 (强化位置保持)"
                    print(f"[{side_name}] 已执行: {action}")
                else:
                    print("❌ 格式错误: 必须指定目标。示例: lock l, limp r, lock b")
            
            # 手部控制
            elif cmd in ['r', 'l', 'b']:
                if len(parts) == 7:
                    try:
                        values = [float(x) for x in parts[1:]]
                        node.set_hand_target(cmd, values)
                        hand_name = {"r": "右手", "l": "左手", "b": "双手"}[cmd]
                        print(f"👉 [{hand_name}] 已接收坐标，后台正以位置环高频锁定...")
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