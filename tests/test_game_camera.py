"""验证独立猜拳 Demo 使用已有 Orbbec 启动命令并只停止自己的进程。"""

import os
import signal
import unittest
from unittest.mock import Mock, patch

from script.game_camera import GameCameraLauncher


class GameCameraTest(unittest.TestCase):
    @patch.dict(os.environ, {
        "GAME_CAMERA_AUTO_START": "true",
        "GAME_CAMERA_LAUNCH_COMMAND": "source install/setup.bash && ros2 launch test_camera demo.py",
        "GAME_CAMERA_LAUNCH_CWD": "/camera/ws",
        "GAME_CAMERA_STARTUP_DELAY_SEC": "0",
    }, clear=False)
    @patch("script.game_camera.os.name", "posix")
    @patch("script.game_camera.subprocess.Popen")
    def test_start_uses_configured_command_and_stop_kills_process_group(self, popen):
        process = Mock()
        process.pid = 1234
        process.poll.side_effect = [None, None, None]
        popen.return_value = process
        launcher = GameCameraLauncher()
        launcher.start()
        popen.assert_called_once_with(
            ["bash", "-lc", "source install/setup.bash && ros2 launch test_camera demo.py"],
            cwd="/camera/ws", start_new_session=True,
            stdout=-3, stderr=-3,
        )
        self.assertTrue(launcher.started)
        with patch("script.game_camera.os.getpgid", return_value=1234, create=True) as getpgid, \
                patch("script.game_camera.os.killpg", create=True) as killpg:
            launcher.stop()
        getpgid.assert_called_once_with(1234)
        killpg.assert_called_once_with(1234, signal.SIGTERM)
        process.wait.assert_called_once_with(timeout=5)

    @patch.dict(os.environ, {"GAME_CAMERA_AUTO_START": "false"}, clear=False)
    @patch("script.game_camera.subprocess.Popen")
    def test_can_use_camera_started_by_another_terminal(self, popen):
        launcher = GameCameraLauncher()
        launcher.start()
        popen.assert_not_called()
        self.assertFalse(launcher.started)


if __name__ == "__main__":
    unittest.main()
