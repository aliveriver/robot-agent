"""验证数据库转换、共享回放器和猜拳对手指的控制，不依赖 ROS2 或机器人。"""

import importlib.util
import io
import json
import sqlite3
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from script.game_trajectory import load_game_trajectory, replay_game_trajectory


class FakeNode:
    """记录发布内容与定时器状态的最小 ROS 节点替身。"""

    def __init__(self, *args):
        self.publishers = []
        self.destroyed = False

    def create_publisher(self, *args):
        pub = Mock()
        self.publishers.append(pub)
        return pub

    def create_timer(self, period, callback):
        self.period = period
        return Mock()

    def get_logger(self):
        return Mock()

    def get_clock(self):
        return Mock()

    def destroy_node(self):
        self.destroyed = True


class FakeMessage:
    """允许原回放器写入 header 和关节命令字段。"""

    def __init__(self):
        self.header = types.SimpleNamespace()


class GamePlaybackTest(unittest.TestCase):
    def setUp(self):
        self.frames = [{"arms": {"11": 0.2}, "lhand": [0.1] * 6, "rhand": [0.8] * 6}]
        self.ros = types.ModuleType("rclpy")
        self.ros.ok = Mock(return_value=True)
        self.ros.shutdown = Mock()
        ros_node = types.ModuleType("rclpy.node")
        ros_node.Node = FakeNode
        body = types.ModuleType("bodyctrl_msgs.msg")
        body.CmdSetMotorPosition = body.SetMotorPosition = FakeMessage
        sensor = types.ModuleType("sensor_msgs.msg")
        sensor.JointState = FakeMessage
        self.modules = patch.dict("sys.modules", {
            "rclpy": self.ros, "rclpy.node": ros_node,
            "bodyctrl_msgs.msg": body, "sensor_msgs.msg": sensor,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        path = Path(__file__).resolve().parents[1] / "script" / "duplicate_path.py"
        spec = importlib.util.spec_from_file_location("script.duplicate_path", path)
        self.player_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.player_module)

    def test_database_frame_conversion_and_missing_id(self):
        # 内存数据库避免测试受机器临时目录权限影响。
        for requested_id in (5, 999):
            with self.subTest(id=requested_id):
                db = sqlite3.connect(":memory:")
                db.executescript("""
                    CREATE TABLE trajectories (id INTEGER, name TEXT, sample_interval_ms INTEGER);
                    CREATE TABLE trajectory_frames (trajectory_id INTEGER, frame_index INTEGER,
                        left_arm TEXT, right_arm TEXT, left_hand TEXT, right_hand TEXT);
                    INSERT INTO trajectories VALUES (5, 'test', 10);
                """)
                values = [[0.1] * 7, [0.2] * 7, [0.3] * 6, [0.4] * 6]
                db.execute("INSERT INTO trajectory_frames VALUES (5, 0, ?, ?, ?, ?)",
                           tuple(map(json.dumps, values)))
                with patch("script.game_trajectory.sqlite3.connect", return_value=db) as connect:
                    if requested_id == 999:
                        with self.assertRaisesRegex(ValueError, "999"):
                            load_game_trajectory(Path("unused.db"), requested_id)
                    else:
                        frames, interval, name = load_game_trajectory(Path("unused.db"), requested_id)
                        self.assertEqual((interval, name), (0.01, "test"))
                        self.assertEqual(frames[0]["arms"]["11"], 0.1)
                        self.assertEqual(frames[0]["arms"]["27"], 0.2)
                        self.assertEqual(frames[0]["lhand"], values[2])
                    self.assertTrue(connect.call_args.kwargs["uri"])
                    self.assertIn("mode=ro", connect.call_args.args[0])

    def test_original_file_playback_publishes_hands_and_shuts_down(self):
        player = self.player_module.PlaybackNode(self.frames)
        with redirect_stdout(io.StringIO()):
            player.play_frame_cb()
            player.play_frame_cb()
        self.assertEqual(player.period, 0.1)
        self.assertTrue(player.finished)
        for pub in player.publishers:
            pub.publish.assert_called_once()
        self.ros.shutdown.assert_called_once()

    def test_game_reuses_player_without_hand_conflict_or_global_shutdown(self):
        self._run_game_replay(fail=False)

    def test_replay_failure_restores_modes_and_destroys_player(self):
        self._run_game_replay(fail=True)

    def test_retraction_reuses_player_in_reverse_and_holds_start_pose(self):
        original = self.frames[:]
        self.frames = [
            {**original[0], "arms": {"11": pos}} for pos in (0.2, 0.4, 0.8)
        ]
        self._run_game_replay(fail=False, reverse=True)
        self.assertEqual([frame['arms']['11'] for frame in self.frames], [0.2, 0.4, 0.8])

    def _run_game_replay(self, fail, reverse=False):
        game = Mock()
        game.arm_mode = {"l": "lock", "r": "idle"}
        game.current_arm_pos = {}
        players = []

        def make_player(*args, **kwargs):
            player = self.player_module.PlaybackNode(*args, **kwargs)
            players.append(player)
            if fail:
                player.arm_cmd_pub.publish.side_effect = RuntimeError("publish failed")
            return player

        def spin_once(node, **kwargs):
            if isinstance(node, FakeNode):
                node.play_frame_cb()
            else:
                node.ctrl_cb()

        self.ros.spin_once = spin_once
        fake_module = types.SimpleNamespace(PlaybackNode=make_player)
        progress = Mock()
        with patch.dict("sys.modules", {"script.duplicate_path": fake_module}), \
                patch("script.game_trajectory.load_game_trajectory", return_value=(self.frames, 0.01, "test")), \
                redirect_stdout(io.StringIO()):
            if fail:
                with self.assertRaisesRegex(RuntimeError, "publish failed"):
                    replay_game_trajectory(game, Path("unused.db"), 5, on_progress=progress)
            else:
                replay_game_trajectory(game, Path("unused.db"), 5, on_progress=progress, reverse=reverse)

        player = players[0]
        self.assertTrue(player.destroyed)
        self.assertEqual(game.current_arm_pos, {} if fail else {11: 0.2})
        self.assertEqual(player.period, 0.01)
        progress.assert_any_call(0.0, len(self.frames) * 0.01)
        expected_frames = list(reversed(self.frames)) if reverse else self.frames
        self.assertEqual(player.trajectory, expected_frames)
        if not fail:
            published = [call.args[0].cmds[0].pos for call in player.arm_cmd_pub.publish.call_args_list]
            self.assertEqual(published, [frame['arms']['11'] for frame in expected_frames])
        player.lhand_cmd_pub.publish.assert_not_called()
        player.rhand_cmd_pub.publish.assert_not_called()
        self.ros.shutdown.assert_not_called()
        game.set_arm_mode.assert_any_call('b', 'idle')
        game.set_arm_mode.assert_any_call('l', 'lock')
        game.set_arm_mode.assert_any_call('r', 'idle')
        game.ctrl_cb.assert_called()


if __name__ == "__main__":
    unittest.main()
