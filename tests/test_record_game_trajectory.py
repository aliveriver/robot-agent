"""验证独立录制复用现有引擎，帧写入同一表结构且失败时完整回滚。"""

import asyncio
import ast
import importlib.util
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from script.record_game_trajectory import record_session, select_for_game


class AsyncDatabase:
    """以真正的内存 SQLite 验证共享 Recorder 发出的 SQL 和事务。"""

    def __init__(self, connection):
        self.connection = connection

    async def execute(self, *args):
        return self.connection.execute(*args)

    async def executemany(self, *args):
        return self.connection.executemany(*args)

    async def commit(self):
        self.connection.commit()

    async def rollback(self):
        self.connection.rollback()

    async def close(self):
        pass


class RecordGameTrajectoryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / 'script/trajectory_manager/db.py').read_text(encoding='utf-8'))
        schema = next(ast.literal_eval(node.value) for node in tree.body
                      if isinstance(node, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'SCHEMA' for t in node.targets))
        self.connection = sqlite3.connect(':memory:')
        self.connection.executescript(schema)
        self.addCleanup(self.connection.close)
        bridge = Mock()
        bridge.get_snapshot.return_value = types.SimpleNamespace(
            left_arm=[0.1] * 7, right_arm=[0.2] * 7,
            left_hand=[0.3] * 6, right_hand=[0.4] * 6,
        )
        self.bridge = bridge
        get_db = AsyncMock(return_value=AsyncDatabase(self.connection))
        mocks = {
            'script.trajectory_manager.db': types.SimpleNamespace(get_db=get_db),
            'script.trajectory_manager.ros_bridge': types.SimpleNamespace(BodySnapshot=object),
        }
        spec = importlib.util.spec_from_file_location(
            'script.trajectory_manager._test_recorder', root / 'script/trajectory_manager/recorder.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', mocks):
            spec.loader.exec_module(module)
        self.recorder = module.Recorder(bridge)

    async def test_session_saves_metadata_and_all_frames_in_shared_schema(self):
        async def wait_stop():
            while len(self.recorder.frames) < 3:
                await asyncio.sleep(0.005)

        trajectory_id = await record_session(self.recorder, self.bridge, 'game', 10, wait_stop)
        row = self.connection.execute(
            'SELECT name, sample_interval_ms, total_frames FROM trajectories WHERE id=?',
            (trajectory_id,),
        ).fetchone()
        self.assertEqual(row, ('game', 10, len(self.recorder.frames)))
        count = self.connection.execute(
            'SELECT count(*) FROM trajectory_frames WHERE trajectory_id=?', (trajectory_id,),
        ).fetchone()[0]
        self.assertEqual(count, len(self.recorder.frames))
        self.assertGreaterEqual(count, 3)
        self.bridge.set_mode.assert_any_call('limp')
        self.bridge.set_mode.assert_called_with('lock')
        self.assertFalse(self.recorder.recording)

    async def test_failed_frame_write_rolls_back_metadata(self):
        self.connection.execute("""CREATE TRIGGER fail_frame BEFORE INSERT ON trajectory_frames
            BEGIN SELECT RAISE(ABORT, 'test write failure'); END;""")
        self.recorder.name = 'failed'
        self.recorder.frames = [{
            'frame_index': 0, 'timestamp_ms': 0.0,
            'left_arm': [0.1] * 7, 'right_arm': [0.2] * 7,
            'left_hand': [0.3] * 6, 'right_hand': [0.4] * 6,
        }]
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'test write failure'):
            await self.recorder._save()
        self.assertEqual(self.connection.execute('SELECT count(*) FROM trajectories').fetchone()[0], 0)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM trajectory_frames').fetchone()[0], 0)

    async def test_empty_session_is_not_selected_as_a_valid_recording(self):
        with self.assertRaisesRegex(RuntimeError, '没有采集'):
            await record_session(self.recorder, self.bridge, 'empty', 10, AsyncMock())
        self.assertEqual(self.connection.execute('SELECT count(*) FROM trajectories').fetchone()[0], 0)
        self.bridge.set_mode.assert_called_with('lock')

    async def test_selection_preserves_existing_environment_settings(self):
        with tempfile.NamedTemporaryFile(mode='w', dir=Path(__file__).resolve().parents[1],
                                         suffix='.test-env', encoding='utf-8', delete=False) as handle:
            env_file = Path(handle.name)
            handle.write('GAME_VISION_MODEL=test-model\nGAME_TRAJECTORY_ID=5\n# keep comment\n')
        try:
            select_for_game(42, env_file)
            text = env_file.read_text(encoding='utf-8')
            self.assertIn('GAME_VISION_MODEL=test-model', text)
            self.assertIn('GAME_TRAJECTORY_ID=42', text)
            self.assertIn('# keep comment', text)
        finally:
            env_file.unlink()


if __name__ == '__main__':
    unittest.main()
