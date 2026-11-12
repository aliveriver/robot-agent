"""阻止导入完整 Agent 配置，验证猜拳 Demo 和复用组件能独立加载。"""

import subprocess
import sys
import unittest
from pathlib import Path


class GameStandaloneTest(unittest.TestCase):
    def test_demo_import_camera_and_audio_need_no_agent_settings(self):
        # 独立子进程复现全新启动，硬件依赖用替身，完整配置模块禁止导入。
        code = '''
import builtins
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import Mock, patch

original_import = builtins.__import__
def reject_settings(name, *args, **kwargs):
    if name == "src.robot_agent.settings":
        raise AssertionError("Standalone demo must not load full Agent settings")
    return original_import(name, *args, **kwargs)
builtins.__import__ = reject_settings
sys.modules["structlog"] = types.SimpleNamespace(get_logger=Mock(return_value=Mock()), BoundLogger=object)
for name in ("numpy", "sounddevice", "sherpa_onnx"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["dotenv"] = types.SimpleNamespace(load_dotenv=lambda *args: None)
sys.modules["rclpy"] = types.ModuleType("rclpy")

class FakeNode:
    def __init__(self, *args):
        pass
    def create_publisher(self, *args):
        return Mock()
    def create_subscription(self, *args):
        return Mock()
    def create_timer(self, *args):
        return Mock()
    def get_clock(self):
        return Mock()

sys.modules["rclpy.node"] = types.SimpleNamespace(Node=FakeNode)
sys.modules["bodyctrl_msgs.msg"] = types.SimpleNamespace(
    MotorStatusMsg=Mock, CmdSetMotorPosition=Mock, SetMotorPosition=Mock)
sys.modules["sensor_msgs.msg"] = types.SimpleNamespace(JointState=Mock)

import script.play_game as demo
assert not hasattr(demo, "logger")
from script.trajectory_manager.arm_currents import ARM_CURRENTS
node = demo.ManipulatorControlNode()
sys.modules["rclpy"].ok = lambda: True
positions = {mid: 0.01 * mid for mid in ARM_CURRENTS}
status = types.SimpleNamespace(status=[types.SimpleNamespace(name=mid, pos=pos)
                                      for mid, pos in positions.items()])
sys.modules["rclpy"].spin_once = Mock(side_effect=lambda *args, **kwargs: node.arm_status_cb(status))
pose = node.capture_arm_pose()
assert pose == positions
node.current_arm_pos[11] = 2.0
assert pose[11] == positions[11]  # 每局保存独立副本。
sys.modules["rclpy"].spin_once = Mock()
node.arm_feedback_at = {mid: 99.0 for mid in ARM_CURRENTS}
with patch("script.play_game.time.monotonic", side_effect=[100, 100, 103]):
    try:
        node.capture_arm_pose()
    except RuntimeError:
        pass
    else:
        raise AssertionError("Stale feedback must not be used as initial pose")
node.set_arm_mode("b", "lock")
node.ctrl_cb()
assert {item.name: item.cur for item in node.arm_cmd_pub.publish.call_args.args[0].cmds} == ARM_CURRENTS
node.set_arm_mode("b", "limp")
node.ctrl_cb()
assert all(item.cur == 0.0 for item in node.arm_cmd_pub.publish.call_args.args[0].cmds)
node.set_hand_target("b", demo.GESTURES["rock"])
node.ctrl_cb()
assert node.lhand_cmd_pub.publish.call_args.args[0].position == [0.0, 0.0, 0.0, 0.0, 0.0, 0.5]
node.set_hand_target("b", demo.GESTURES["paper"])
node.ctrl_cb()
assert node.lhand_cmd_pub.publish.call_args.args[0].position == [1.0] * 6
# 录制后锁臂和轨迹管理器回放同样使用共享配置，拖动时保持零电流。
from script.trajectory_manager.ros_bridge import RosBridge, _BridgeNode, BodySnapshot
bridge = RosBridge.__new__(RosBridge)
bridge._node = _BridgeNode()
bridge.set_mode("lock")
bridge._ctrl_tick()
assert {item.name: item.cur for item in bridge._node.arm_cmd_pub.publish.call_args.args[0].cmds} == ARM_CURRENTS
bridge.send_frame(BodySnapshot())
assert {item.name: item.cur for item in bridge._node.arm_cmd_pub.publish.call_args.args[0].cmds} == ARM_CURRENTS
bridge.set_mode("limp")
bridge._ctrl_tick()
assert all(item.cur == 0.0 for item in bridge._node.arm_cmd_pub.publish.call_args.args[0].cmds)
from src.robot_agent.capabilities.vision.ros_camera import get_camera_provider
camera = get_camera_provider(topic="/test/image", timeout=2.0, save_dir="data/camera",
                             local_device_index=0, prefer_ros=True)
assert camera._topic == "/test/image"
assert camera is get_camera_provider()
# 视觉请求期间控制节点仍在 spin，相机必须使用自己的执行器。
executor = Mock()
executor.spin_once.side_effect = lambda **kwargs: camera._ros_stop_event.set()
sys.modules["rclpy.executors"] = types.SimpleNamespace(
    SingleThreadedExecutor=Mock(return_value=executor))
sys.modules["rclpy"].spin_once = Mock(side_effect=AssertionError("Shared executor used"))
camera._ros_node = object()
camera._spin_ros_node()
executor.add_node.assert_called_once_with(camera._ros_node)
executor.spin_once.assert_called_once_with(timeout_sec=0.1)
executor.shutdown.assert_called_once()
sys.modules["rclpy"].spin_once.assert_not_called()
from src.robot_agent.interfaces.audio.playback_controller import AudioPlaybackController
player = AudioPlaybackController(output_device=None)
assert player.status()["state"] == "idle"
# 即使默认 Agent 组件用法加载配置，也保持原有行为，不悄悄吞配置错误。
from src.robot_agent.capabilities.vision.ros_camera import RosCameraProvider
try:
    RosCameraProvider()
except AssertionError:
    pass
else:
    raise AssertionError("Default Agent camera should still read its settings")
assert "src.robot_agent.settings" not in sys.modules
print("Independent demo imports, camera and audio: OK")
'''
        result = subprocess.run([sys.executable, "-c", code],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
