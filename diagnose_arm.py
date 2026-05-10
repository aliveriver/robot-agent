#!/usr/bin/env python3
"""
诊断脚本：验证 robot-agent 的 ROS2 机械臂接口
在机器人上运行：python3 diagnose_arm.py

会依次检查：
  1. rclpy / bodyctrl_msgs 是否可导入
  2. /arm/status 是否有数据（以及 MotorStatusMsg 字段结构）
  3. /arm/cmd_pos 是否存在
  4. 向 /arm/cmd_pos 发一条 hold-current 指令（不移动，只重发当前位置）
"""

import sys
import time

# ── 1. 导入检查 ─────────────────────────────────────────────────
print("=" * 60)
print("Step 1: 检查依赖导入")
print("=" * 60)

try:
    import rclpy
    from rclpy.node import Node
    print("  [OK] rclpy 可导入")
except ImportError as e:
    print(f"  [FAIL] rclpy 不可导入: {e}")
    sys.exit(1)

try:
    from bodyctrl_msgs.msg import MotorStatusMsg, CmdSetMotorPosition, SetMotorPosition
    print("  [OK] bodyctrl_msgs 可导入 (MotorStatusMsg, CmdSetMotorPosition, SetMotorPosition)")
except ImportError as e:
    print(f"  [FAIL] bodyctrl_msgs 不可导入: {e}")
    print("         请在 ROS2 workspace 编译后执行 source install/setup.bash")
    sys.exit(1)

try:
    from sensor_msgs.msg import JointState
    print("  [OK] sensor_msgs.JointState 可导入")
except ImportError as e:
    print(f"  [FAIL] sensor_msgs 不可导入: {e}")
    sys.exit(1)

# ── 2. 订阅 /arm/status 验证字段 ───────────────────────────────
print()
print("=" * 60)
print("Step 2: 订阅 /arm/status（等待 5 秒）")
print("=" * 60)

rclpy.init()

received_msg = []

class DiagNode(Node):
    def __init__(self):
        super().__init__("arm_diag_node")
        self.status_sub = self.create_subscription(
            MotorStatusMsg, "/arm/status", self._on_status, 10
        )
        self.cmd_pub = self.create_publisher(
            CmdSetMotorPosition, "/arm/cmd_pos", 10
        )

    def _on_status(self, msg):
        if not received_msg:
            received_msg.append(msg)

node = DiagNode()

deadline = time.time() + 5.0
while time.time() < deadline and not received_msg:
    rclpy.spin_once(node, timeout_sec=0.1)

if not received_msg:
    print("  [FAIL] 5 秒内未收到 /arm/status 消息")
    print("         请确认：ros2 topic list | grep arm")
else:
    msg = received_msg[0]
    print(f"  [OK] 收到 /arm/status 消息")

    # 检查 msg.status
    items = getattr(msg, "status", None)
    if items is None:
        print(f"  [WARN] msg.status 字段不存在，实际顶层字段：")
        fields = [f for f in dir(msg) if not f.startswith("_")]
        print(f"         {fields}")
    else:
        print(f"  [OK] msg.status 包含 {len(items)} 个电机项")
        if items:
            sample = items[0]
            fields = [f for f in dir(sample) if not f.startswith("_")]
            print(f"  [INFO] MotorStatus item 字段: {fields}")
            print(f"  [INFO] 示例: name={getattr(sample,'name','N/A')}  "
                  f"pos={getattr(sample,'pos','N/A'):.4f}  "
                  f"spd={getattr(sample,'spd','N/A'):.4f}  "
                  f"tor={getattr(sample,'tor','N/A'):.4f}")

            # 打印所有电机 ID 和当前位置
            print()
            print("  电机 ID -> 当前位置（rad）：")
            for item in items:
                mid = getattr(item, "name", "?")
                pos = getattr(item, "pos", 0.0)
                print(f"    Motor {mid:>3} : {pos:+.4f} rad")

# ── 3. 测试发送 hold-current 到 /arm/cmd_pos ───────────────────
print()
print("=" * 60)
print("Step 3: 向 /arm/cmd_pos 发送 hold-current 指令（右臂）")
print("        不会产生运动，只是重发当前位置")
print("=" * 60)

if received_msg:
    msg = received_msg[0]
    items = getattr(msg, "status", []) or []
    RIGHT_IDS = list(range(21, 28))

    # 构建当前位置映射
    pos_map = {}
    for item in items:
        try:
            pos_map[int(item.name)] = float(item.pos)
        except Exception:
            pass

    cmd_msg = CmdSetMotorPosition()
    cmd_msg.header.stamp = node.get_clock().now().to_msg()
    cmd_msg.header.frame_id = "arm_diag"
    cmd_msg.cmds = []

    missing = []
    for mid in RIGHT_IDS:
        pos = pos_map.get(mid)
        if pos is None:
            missing.append(mid)
            pos = 0.0
        item = SetMotorPosition()
        item.name = int(mid)
        item.pos  = float(pos)
        item.spd  = 5.0    # 很低速度
        item.cur  = 0.5    # 很低电流
        cmd_msg.cmds.append(item)

    if missing:
        print(f"  [WARN] 以下电机 ID 未在 status 中出现，用 0.0 代替: {missing}")

    for _ in range(5):
        node.cmd_pub.publish(cmd_msg)
        rclpy.spin_once(node, timeout_sec=0.0)
        time.sleep(0.1)

    print(f"  [OK] 已发送 5 帧 hold-current 指令到 /arm/cmd_pos")
    print(f"       目标位置（右臂）: {[round(pos_map.get(m,0.0),4) for m in RIGHT_IDS]}")
else:
    print("  [SKIP] 未收到状态，跳过发送测试")

# ── 清理 ─────────────────────────────────────────────────────────
print()
node.destroy_node()
rclpy.shutdown()
print("诊断完成。请将以上输出发给开发者。")
