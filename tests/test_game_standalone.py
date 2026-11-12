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
from unittest.mock import Mock

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
sys.modules["rclpy.node"] = types.SimpleNamespace(Node=object)
sys.modules["bodyctrl_msgs.msg"] = types.SimpleNamespace(
    MotorStatusMsg=object, CmdSetMotorPosition=object, SetMotorPosition=object)
sys.modules["sensor_msgs.msg"] = types.SimpleNamespace(JointState=object)

import script.play_game as demo
assert not hasattr(demo, "logger")
from src.robot_agent.capabilities.vision.ros_camera import get_camera_provider
camera = get_camera_provider(topic="/test/image", timeout=2.0, save_dir="data/camera",
                             local_device_index=0, prefer_ros=True)
assert camera._topic == "/test/image"
assert camera is get_camera_provider()
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
