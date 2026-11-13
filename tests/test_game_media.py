"""Verify preset selection, outcome speech and exact vision frame persistence."""

import base64
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from script.game_audio import resolve_game_audio
from script.game_image import save_game_image
from script.game_round import GameTiming, play_result_audio


class GameMediaTest(unittest.TestCase):
    def test_select_named_presets_and_newest_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'GAME_AUDIO_DB': 'data/tts_audio.sqlite3', 'GAME_AUDIO_DIR': 'data/tts_audio',
            'GAME_COUNTDOWN_PRESET': '石头剪刀布', 'GAME_PLAYER_WIN_PRESET': '你赢了',
            'GAME_ROBOT_WIN_PRESET': '我赢了',
        }):
            root = Path(tmp)
            directory = root / 'data/tts_audio'
            directory.mkdir(parents=True)
            with sqlite3.connect(root / 'data/tts_audio.sqlite3') as conn:
                conn.execute('CREATE TABLE preset_audio (name TEXT, filename TEXT, created_at TEXT)')
                for name, filename, created in [('石头剪刀布','old.wav','2025'),
                        ('石头剪刀布','count.wav','2026'), ('你赢了','win.wav','2026'),
                        ('我赢了','lose.wav','2026')]:
                    conn.execute('INSERT INTO preset_audio VALUES (?,?,?)', (name,filename,created))
                    (directory / filename).write_bytes(b'test')
            for cue, filename in [('countdown','count.wav'), ('win','win.wav'), ('lose','lose.wav')]:
                self.assertEqual(resolve_game_audio(root, cue), directory / filename)
            (directory / 'win.wav').unlink()
            with self.assertRaises(FileNotFoundError):
                resolve_game_audio(root, 'win')
            with patch.dict(os.environ, {'GAME_PLAYER_WIN_PRESET': 'missing'}):
                with self.assertRaises(LookupError):
                    resolve_game_audio(root, 'win')

    def test_result_audio_uses_human_perspective_and_keeps_ros_spinning(self):
        for outcome in ('win', 'lose'):
            with self.subTest(outcome=outcome):
                audio, node = Mock(), Mock()
                audio.finished.side_effect = [False, True]
                with patch('script.game_round.resolve_game_audio', return_value=Path('result.wav')) as resolve, \
                        patch('script.game_round.CountdownAudio', return_value=audio) as factory, \
                        patch('script.game_round.wait_with_ros') as wait:
                    timing = GameTiming()
                    play_result_audio(node, Path('repo'), outcome, timing)
                resolve.assert_called_once_with(Path('repo'), outcome)
                factory.assert_called_once_with(Path('result.wav'), timing, source_type='game_result')
                audio.start.assert_called_once()
                wait.assert_called_once_with(node, 0.01)
                audio.stop.assert_called_once()

    def test_result_playback_error_still_stops_audio(self):
        audio = Mock()
        audio.finished.side_effect = RuntimeError('speaker failed')
        with patch('script.game_round.resolve_game_audio', return_value=Path('result.wav')), \
                patch('script.game_round.CountdownAudio', return_value=audio):
            with self.assertRaisesRegex(RuntimeError, 'speaker failed'):
                play_result_audio(Mock(), Path('repo'), 'win', GameTiming())
        audio.stop.assert_called_once()

    def test_failed_vision_api_still_leaves_current_png(self):
        import ast
        import asyncio
        import json
        import types
        import cv2
        import numpy as np

        ok, jpeg = cv2.imencode('.jpg', np.full((8, 12, 3), 100, dtype=np.uint8))
        self.assertTrue(ok)

        async def capture():
            return base64.b64encode(jpeg.tobytes()).decode()

        provider = types.SimpleNamespace(get_camera_provider=lambda **kwargs: types.SimpleNamespace(capture_base64=capture))
        request = Mock()
        request.urlopen.side_effect = RuntimeError('API unavailable')
        tree = ast.parse(Path('script/play_game.py').read_text())
        judge = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'judge_game_result')
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'GAME_CAPTURE_IMAGE_PATH': 'script/temp.png'}), \
                patch.dict('sys.modules', {'src.robot_agent.capabilities.vision.ros_camera': provider}):
            env = dict(os=os, asyncio=asyncio, json=json, urllib=types.SimpleNamespace(request=request),
                       _ROBOT_AGENT_ROOT=Path(tmp), GAME_VISION_API_URL='https://example.test',
                       GAME_VISION_API_KEY='test', GAME_VISION_MODEL='test')
            exec(compile(ast.Module(body=[judge], type_ignores=[]), 'judge_game_result', 'exec'), env)
            result = env['judge_game_result']('rock')
            self.assertEqual(result['result'], 'unknown')
            self.assertTrue((Path(tmp) / 'script/temp.png').read_bytes().startswith(b'\x89PNG'))
            request.urlopen.assert_called_once()

    def test_camera_jpeg_is_saved_as_png_and_overwritten(self):
        import cv2
        import numpy as np

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'GAME_CAPTURE_IMAGE_PATH': 'script/temp.png'}):
            root = Path(tmp)
            for value in (25, 200):
                image = np.full((8, 12, 3), value, dtype=np.uint8)
                ok, jpeg = cv2.imencode('.jpg', image)
                self.assertTrue(ok)
                path = save_game_image(base64.b64encode(jpeg.tobytes()).decode(), root)
                self.assertEqual(path, root / 'script/temp.png')
                self.assertTrue(path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
                actual = cv2.imread(str(path))
                np.testing.assert_array_equal(actual, image)
            previous = path.read_bytes()
            with self.assertRaises(ValueError):
                save_game_image('invalid base64!', root)
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(list(path.parent.glob('.game-image-*')), [])


if __name__ == '__main__':
    unittest.main()
