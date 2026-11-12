"""离线验证播放器区分播完、主动停止和设备失败，供猜拳时序复用。"""

import importlib.util
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch


class AudioPlaybackControllerTest(unittest.TestCase):
    def setUp(self):
        settings = types.SimpleNamespace(audio=types.SimpleNamespace(output_device=None))
        audio = MagicMock()
        audio.__len__.return_value = 1040
        audio.samplerate, audio.channels = 32000, 1
        audio.read.side_effect = [[0] * 1024, [0] * 16, []]
        self.audio = audio
        sound_file = Mock()
        sound_file.__enter__ = Mock(return_value=audio)
        sound_file.__exit__ = Mock(return_value=False)
        sound_file.__len__ = Mock(return_value=1040)
        sound_file.samplerate = 32000
        output = Mock()
        output.__enter__ = Mock(return_value=output)
        output.__exit__ = Mock(return_value=False)
        self.output = output
        self.sd = types.SimpleNamespace(OutputStream=Mock(return_value=output))
        modules = {
            "src.robot_agent.settings": types.SimpleNamespace(settings=settings),
            "src.robot_agent.interfaces.audio.device_resolver": types.SimpleNamespace(
                resolve_output_device=lambda device: None),
            "soundfile": types.SimpleNamespace(SoundFile=Mock(return_value=sound_file)),
            "sounddevice": self.sd,
        }
        self.modules = patch.dict("sys.modules", modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        path = Path(__file__).resolve().parents[1] / "src/robot_agent/interfaces/audio/playback_controller.py"
        spec = importlib.util.spec_from_file_location("test_playback_controller", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.controller = module.AudioPlaybackController()

    def _start(self):
        with patch.object(Path, "is_file", return_value=True), patch("threading.Thread"):
            return self.controller.play("countdown.wav", "game_countdown")["session_id"]

    def test_success_is_reported_after_output_stream_has_closed(self):
        session = self._start()
        self.controller._run(session)
        self.assertEqual(self.output.write.call_count, 2)
        self.output.__exit__.assert_called_once()
        status = self.controller.status()
        self.assertEqual(status["state"], "idle")
        self.assertEqual(status["last_completed_session_id"], session)
        self.assertIsNone(status["last_error"])

    def test_device_failure_is_reported_and_never_marked_complete(self):
        self.sd.OutputStream.side_effect = RuntimeError("speaker unavailable")
        session = self._start()
        self.controller._run(session)
        status = self.controller.status()
        self.assertEqual(status["state"], "idle")
        self.assertEqual(status["last_error"], "speaker unavailable")
        self.assertIsNone(status["last_completed_session_id"])

    def test_stopped_session_is_not_marked_complete(self):
        session = self._start()
        self.controller.stop()
        self.controller._run(session)
        self.sd.OutputStream.assert_not_called()
        self.assertIsNone(self.controller.status()["last_completed_session_id"])


if __name__ == "__main__":
    unittest.main()
