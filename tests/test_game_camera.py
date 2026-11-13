"""Regression checks for camera contention, readiness and startup cleanup."""

import ast
import os
import signal
import subprocess
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, patch

from script.game_camera import GameCameraLauncher


@patch.dict(os.environ, {
    'GAME_CAMERA_AUTO_START': 'true',
    'GAME_CAMERA_LAUNCH_COMMAND': 'source install/setup.bash && ros2 launch test_camera demo.py',
    'GAME_CAMERA_LAUNCH_CWD': '/camera/ws',
    'GAME_CAMERA_STARTUP_DELAY_SEC': '0',
}, clear=False)
class GameCameraTest(unittest.TestCase):
    @patch.object(GameCameraLauncher, '_existing_driver_pids', return_value=[])
    @patch('script.game_camera.subprocess.Popen')
    def test_start_and_stop_owned_process_group(self, popen, existing):
        process = Mock(pid=1234)
        process.poll.return_value = None
        popen.return_value = process
        launcher = GameCameraLauncher()
        launcher.start()
        popen.assert_called_once_with(
            ['bash', '-lc', os.environ['GAME_CAMERA_LAUNCH_COMMAND']],
            cwd='/camera/ws', start_new_session=True,
            stdout=ANY, stderr=subprocess.STDOUT,
        )
        log_path = Path(launcher._log.name)
        self.assertTrue(launcher.started)
        try:
            with patch('script.game_camera.os.killpg', create=True) as killpg:
                launcher.stop()
            self.assertEqual(killpg.call_args_list[0].args, (1234, signal.SIGTERM))
            process.wait.assert_called_once_with(timeout=5)
            self.assertIsNone(launcher._log)
        finally:
            log_path.unlink(missing_ok=True)

    @patch('script.game_camera.subprocess.Popen')
    def test_disabled_auto_start(self, popen):
        with patch.dict(os.environ, {'GAME_CAMERA_AUTO_START': 'false'}):
            GameCameraLauncher().start()
        popen.assert_not_called()

    @patch.object(GameCameraLauncher, '_wait_for_image')
    @patch.object(GameCameraLauncher, '_camera_node_exists', return_value=True)
    @patch('script.game_camera.subprocess.Popen')
    def test_existing_node_is_reused_and_verified(self, popen, exists, wait):
        node = Mock()
        launcher = GameCameraLauncher()
        launcher.start(node)
        wait.assert_called_once_with(node, 15.0)
        popen.assert_not_called()
        self.assertFalse(launcher.started)

    @patch.object(GameCameraLauncher, '_existing_driver_pids', return_value=[7906])
    @patch.object(GameCameraLauncher, '_camera_node_exists', return_value=False)
    @patch('script.game_camera.subprocess.Popen')
    def test_undiscovered_driver_blocks_duplicate_launch(self, popen, exists, pids):
        with self.assertRaisesRegex(RuntimeError, '7906'):
            GameCameraLauncher().start(Mock())
        popen.assert_not_called()

    @patch.object(GameCameraLauncher, '_wait_for_image', side_effect=RuntimeError('no frames'))
    @patch.object(GameCameraLauncher, '_existing_driver_pids', return_value=[])
    @patch.object(GameCameraLauncher, '_camera_node_exists', return_value=False)
    @patch('script.game_camera.subprocess.Popen')
    def test_readiness_failure_stops_owned_driver(self, popen, exists, pids, wait):
        process = Mock(pid=1234)
        process.poll.return_value = None
        popen.return_value = process
        launcher = GameCameraLauncher()
        with patch('script.game_camera.os.killpg', create=True) as killpg:
            with self.assertRaisesRegex(RuntimeError, 'no frames'):
                launcher.start(Mock())
        self.assertEqual(killpg.call_args_list[0].args, (1234, signal.SIGTERM))
        self.assertIsNone(launcher._process)
        self.assertIsNone(launcher._log)

    @patch('script.game_camera.subprocess.Popen')
    def test_invalid_delay_never_spawns_driver(self, popen):
        with patch.dict(os.environ, {'GAME_CAMERA_STARTUP_DELAY_SEC': '-1'}):
            with self.assertRaises(ValueError):
                GameCameraLauncher().start()
        popen.assert_not_called()

    def test_stop_escalates_when_owned_driver_hangs(self):
        launcher = GameCameraLauncher()
        launcher._process = Mock(pid=1234)
        launcher._process.wait.side_effect = [subprocess.TimeoutExpired('camera', 5), None]
        with patch('script.game_camera.os.killpg', create=True) as killpg:
            launcher.stop()
        self.assertEqual(killpg.call_args_list[0].args, (1234, signal.SIGTERM))
        self.assertEqual(killpg.call_args_list[1].args, (1234, signal.SIGKILL))


class GameStartupCleanupTest(unittest.TestCase):
    def test_microphone_failure_closes_camera_and_ros(self):
        # Execute only main() with mocks: this test cannot publish robot commands.
        tree = ast.parse(Path('script/play_game.py').read_text())
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        module = ast.Module(body=[main], type_ignores=[])
        node, camera, ros, audio = Mock(), Mock(), Mock(), Mock()
        audio.InputStream.side_effect = RuntimeError('microphone busy')
        env = dict(initSherpaOnnx=lambda: True, rclpy=ros, ManipulatorControlNode=lambda: node,
                   GameCameraLauncher=lambda: camera, sd=audio, audio_cfg=Mock(),
                   trigger_keywords=[], GAME_TRAJECTORY_DB='test.db', GAME_TRAJECTORY_ID='5')
        exec(compile(module, 'play_game_main', 'exec'), env)
        with self.assertRaisesRegex(RuntimeError, 'microphone busy'):
            env['main']()
        camera.stop.assert_called_once()
        node.destroy_node.assert_called_once()
        ros.shutdown.assert_called_once()


if __name__ == '__main__':
    unittest.main()
