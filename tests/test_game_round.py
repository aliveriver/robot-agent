"""验证本地口令在轨迹内播放，以及出手、观察、判定和复位的先后关系。"""

import types
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from script.game_round import CountdownAudio, GameTiming, run_game_round, wait_with_ros


class GameRoundTest(unittest.TestCase):
    def test_final_gesture_follows_trajectory_and_audio_then_observation_and_judgment(self):
        events = []
        node = Mock()
        node.set_hand_target.side_effect = lambda side, values: events.append(("hand", values))
        gestures = {"rock": [1], "scissors": [2], "paper": [0]}
        countdown = Mock()
        countdown.finished.side_effect = [False, True]
        countdown.on_progress.side_effect = lambda elapsed, total: events.append("audio_during_motion")

        def replay(*args, on_progress):
            events.append("trajectory_begin")
            on_progress(15.08, 16.58)
            events.append("trajectory_end")

        def wait(node, seconds):
            events.append(("wait", seconds))

        def judge(name):
            events.append(("judge", name))
            return {"result": "draw", "player_gesture": "scissors"}

        with patch.dict("os.environ", {"GAME_COUNTDOWN_AUDIO": "data/countdown.wav"}), \
                patch("script.game_round.GameTiming.from_env", return_value=GameTiming(reveal_sec=0.2)), \
                patch("script.game_round.CountdownAudio", return_value=countdown) as audio_factory, \
                patch("script.game_round.replay_game_trajectory", side_effect=replay), \
                patch("script.game_round.wait_with_ros", side_effect=wait), \
                patch("script.game_round.random.choice", return_value=("scissors", [2])), \
                redirect_stdout(StringIO()):
            result = run_game_round(node, gestures=gestures, judge_result=judge,
                                    db_path=Path("track.db"), trajectory_id=5, root=Path("repo"))

        self.assertEqual(events, [
            ("hand", [1]), ("wait", 3.0), "trajectory_begin", "audio_during_motion",
            "trajectory_end", ("wait", 0.01), ("wait", 0.2), ("hand", [2]),
            ("wait", 2.0), ("judge", "scissors"), ("hand", [0]),
        ])
        self.assertEqual(result["result"], "draw")
        self.assertEqual(audio_factory.call_args.args[0], Path("repo/data/countdown.wav"))
        countdown.stop.assert_called_once()

    def test_replay_failure_cancels_audio_and_does_not_reveal_or_judge(self):
        node, judge, countdown = Mock(), Mock(), Mock()
        with patch("script.game_round.GameTiming.from_env", return_value=GameTiming()), \
                patch("script.game_round.CountdownAudio", return_value=countdown), \
                patch("script.game_round.wait_with_ros"), \
                patch("script.game_round.replay_game_trajectory", side_effect=RuntimeError("motion failed")), \
                redirect_stdout(StringIO()):
            with self.assertRaisesRegex(RuntimeError, "motion failed"):
                run_game_round(node, gestures={"rock": [1], "paper": [0]}, judge_result=judge,
                               db_path=Path("track.db"), trajectory_id=5, root=Path("repo"))
        self.assertEqual(node.set_hand_target.call_count, 2)
        node.set_hand_target.assert_called_with('b', [0])
        judge.assert_not_called()
        countdown.stop.assert_called_once()

    def test_audio_failure_cancels_round_before_reveal(self):
        node, judge, countdown = Mock(), Mock(), Mock()
        countdown.finished.side_effect = RuntimeError("speaker failed")
        with patch("script.game_round.GameTiming.from_env", return_value=GameTiming()), \
                patch("script.game_round.CountdownAudio", return_value=countdown), \
                patch("script.game_round.wait_with_ros"), \
                patch("script.game_round.replay_game_trajectory"), \
                redirect_stdout(StringIO()):
            with self.assertRaisesRegex(RuntimeError, "speaker failed"):
                run_game_round(node, gestures={"rock": [1], "paper": [0]}, judge_result=judge,
                               db_path=Path("track.db"), trajectory_id=5, root=Path("repo"))
        judge.assert_not_called()
        countdown.stop.assert_called_once()
        node.set_hand_target.assert_called_with('b', [0])

    def test_countdown_starts_once_near_end_using_actual_file_duration(self):
        controller = Mock()
        controller.play.return_value = {"session_id": "audio1"}
        controller.status.return_value = {"state": "playing", "session_id": "audio1"}
        modules = {
            "soundfile": types.SimpleNamespace(info=Mock(return_value=types.SimpleNamespace(
                frames=48000, samplerate=32000))),
            "src.robot_agent.interfaces.audio.playback_controller": types.SimpleNamespace(
                get_playback_controller=lambda: controller),
        }
        with patch.dict("sys.modules", modules), redirect_stdout(StringIO()):
            countdown = CountdownAudio(Path("countdown.wav"), GameTiming(countdown_sec=99))
            countdown.on_progress(15.0, 16.58)
            controller.play.assert_not_called()
            countdown.on_progress(15.1, 16.58)
            countdown.on_progress(16.0, 16.58)
            self.assertFalse(countdown.finished())
            controller.status.return_value = {
                "state": "idle", "last_completed_session_id": "audio1",
            }
            self.assertTrue(countdown.finished())
        controller.play.assert_called_once_with(Path("countdown.wav"), "game_countdown")
        self.assertEqual(countdown.duration, 1.5)

    def test_long_audio_starts_with_trajectory_and_errors_are_not_treated_as_completion(self):
        controller = Mock()
        controller.play.return_value = {"session_id": "audio1"}
        controller.status.return_value = {"state": "playing", "session_id": "audio1"}
        countdown = CountdownAudio(None, GameTiming(countdown_sec=5))
        countdown.controller = controller
        with redirect_stdout(StringIO()):
            countdown.on_progress(0, 2)
        controller.play.assert_called_once()
        self.assertFalse(countdown.finished())
        controller.status.return_value = {"state": "idle", "last_error": "device unavailable"}
        with self.assertRaisesRegex(RuntimeError, "device unavailable"):
            countdown.finished()
        controller.status.return_value = {"state": "idle", "last_completed_session_id": None}
        with self.assertRaisesRegex(RuntimeError, "被中断"):
            countdown.finished()

    def test_ros_wait_keeps_processing_control_callbacks(self):
        ros = types.SimpleNamespace(ok=lambda: True, spin_once=Mock())
        with patch.dict("sys.modules", {"rclpy": ros}), \
                patch("script.game_round.time.monotonic", side_effect=[0, 0.5, 0.5, 1.1]):
            wait_with_ros(Mock(), 1.0)
        ros.spin_once.assert_called_once()

    def test_rejects_invalid_wait_configuration(self):
        for value in ("-1", "nan", "inf"):
            with self.subTest(value=value), patch.dict("os.environ", {"GAME_READY_DELAY_SEC": value}):
                with self.assertRaises(ValueError):
                    GameTiming.from_env()


if __name__ == "__main__":
    unittest.main()
