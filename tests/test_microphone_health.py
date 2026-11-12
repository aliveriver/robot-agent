"""Validate actual listener-thread readiness with an isolated fake input stream."""
import importlib.util
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class MicrophoneHealthTest(unittest.TestCase):
    """Mock device I/O only; exercise the original listener's real thread lifecycle."""

    def listener(self):
        spec = importlib.util.spec_from_file_location('microphone_under_test',
            Path(__file__).resolve().parents[1] / 'src/robot_agent/interfaces/audio/microphone.py')
        module = importlib.util.module_from_spec(spec)
        stubs = {
            'numpy': SimpleNamespace(ndarray=object),
            'src.robot_agent.bootstrap.logging': SimpleNamespace(get_logger=lambda _: Mock()),
            'src.robot_agent.settings': SimpleNamespace(settings=SimpleNamespace(
                audio=SimpleNamespace(input_device=None, sample_rate=16000, min_segment_sec=0.1,
                                      blocksize=160, speech_threshold=0.1))),
            'src.robot_agent.interfaces.audio.device_resolver': SimpleNamespace(
                device_label=Mock(), log_audio_device_selection=Mock(), resolve_input_device=Mock()),
        }
        with patch.dict(sys.modules, stubs):
            spec.loader.exec_module(module)
        return module.MicrophoneListener(lambda frames: None)

    def test_ready_requires_live_thread_and_active_stream_and_detects_loss(self):
        mic = self.listener()
        stream = SimpleNamespace(active=True, stop=Mock(), close=Mock())
        self.assertFalse(mic.is_ready())
        mic._start_input_stream = lambda: setattr(mic, '_stream', stream)
        try:
            mic.start()
            for _ in range(100):
                if mic.is_ready():
                    break
                time.sleep(0.001)
            self.assertTrue(mic.is_ready())
            stream.active = False
            self.assertFalse(mic.is_ready())
        finally:
            mic.stop(flush=False)

    def test_stream_start_failure_is_observable_and_thread_stops(self):
        mic = self.listener()
        mic._start_input_stream = Mock(side_effect=RuntimeError('device unplugged'))
        mic.start()
        mic._worker_thread.join(timeout=1)
        self.assertTrue(mic.has_failed())
        self.assertFalse(mic.is_ready())
        mic.stop(flush=False)

    def test_recovery_shutdown_discards_buffered_speech(self):
        mic = self.listener()
        mic._current_audio_frames = [object()]
        mic._flush_current_segment = Mock()
        mic.stop(flush=False)
        mic._flush_current_segment.assert_not_called()
        self.assertEqual(mic._current_audio_frames, [])


if __name__ == '__main__':
    unittest.main()
