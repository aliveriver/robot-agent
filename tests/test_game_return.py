"""验证回位使用本局保存姿态、实测起点及缓起缓停曲线，不读取录制轨迹。"""

import os
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

from script.game_return import build_return_frames, return_to_initial_pose


class GameReturnTest(unittest.TestCase):
    def test_smooth_return_preserves_endpoints_and_limits_speed(self):
        start, target = {11: 1.5, 21: -1.0}, {11: -0.5, 21: 0.2}
        frames, duration = build_return_frames(start, target, 4.0, 0.35)
        self.assertGreaterEqual(duration, 1.875 * 2 / 0.35)
        self.assertEqual(frames[0]['arms'], {'11': 1.5, '21': -1.0})
        self.assertEqual(frames[-1]['arms'], {'11': -0.5, '21': 0.2})
        for mid in ('11', '21'):
            velocities = [(b['arms'][mid] - a['arms'][mid]) / 0.02
                          for a, b in zip(frames, frames[1:])]
            self.assertLessEqual(max(map(abs, velocities)), 0.35)
            self.assertLess(abs(velocities[0]), 0.001)
            self.assertLess(abs(velocities[-1]), 0.001)
            self.assertTrue(all(v <= 0 for v in velocities) if mid == '11'
                            else all(v >= 0 for v in velocities))

    def test_return_uses_measured_pose_and_saved_target(self):
        node = Mock()
        node.capture_arm_pose.return_value = {11: 0.8, 21: -0.4}
        initial = {11: 0.1, 21: 0.2}
        with patch.dict(os.environ, {'GAME_RETURN_DURATION_SEC': '5', 'GAME_RETURN_SPEED_RAD_SEC': '0.2'}), \
                patch('script.game_return.play_arm_frames') as play, redirect_stdout(StringIO()):
            return_to_initial_pose(node, initial)
        node.capture_arm_pose.assert_called_once_with()
        args, kwargs = play.call_args
        self.assertIs(args[0], node)
        self.assertEqual(args[1][0]['arms'], {'11': 0.8, '21': -0.4})
        self.assertAlmostEqual(args[1][-1]['arms']['11'], 0.1)
        self.assertAlmostEqual(args[1][-1]['arms']['21'], 0.2)
        self.assertGreaterEqual((len(args[1]) - 1) * args[2], 5)
        self.assertEqual(kwargs['speed'], 0.2)
        self.assertEqual(initial, {11: 0.1, 21: 0.2})

    def test_invalid_return_settings_cannot_start_motion(self):
        for value in (-1, 0, float('nan'), float('inf')):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    build_return_frames({11: 1.0}, {11: 0.0}, value, 0.35)
                with self.assertRaises(ValueError):
                    build_return_frames({11: 1.0}, {11: 0.0}, 4, value)


if __name__ == '__main__':
    unittest.main()
