"""轨迹状态机的离线测试，不依赖 ROS2。"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from src.robot_agent.interfaces.trajectory.manager import TrajectoryManager
from src.robot_agent.interfaces.trajectory.storage import TrajectoryStore


class FakeHardware:
    def __init__(self) -> None:
        self.counter = 0
        self.published = []

    def snapshot(self):
        self.counter += 1
        return {
            "arms": {str(i): self.counter / 10 for i in list(range(11, 18)) + list(range(21, 28))},
            "lhand": [0.1] * 6,
            "rhand": [0.2] * 6,
        }

    def set_teach_mode(self):
        return None

    def publish_frame(self, frame, speed_scale):
        self.published.append((frame, speed_scale))


class TrajectoryManagerTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        store = TrajectoryStore(Path(self.tempdir.name) / "test.sqlite3")
        self.hardware = FakeHardware()
        self.manager = TrajectoryManager(store=store, hardware=self.hardware)

    def tearDown(self):
        self.manager.stop_active()
        self.tempdir.cleanup()

    def test_record_persist_and_replay(self):
        self.manager.start_record(name="挥手", sample_interval=0.02, max_duration=1)
        time.sleep(0.08)
        self.manager.stop_record()
        trajectories = self.manager.list_trajectories()
        self.assertEqual(len(trajectories), 1)
        self.assertGreaterEqual(trajectories[0]["frame_count"], 2)

        self.manager.start_replay(
            trajectory_id=trajectories[0]["id"],
            speed_scale=2,
            smoothing=0.2,
            repeat_count=2,
            safety_confirmed=True,
        )
        while self.manager.status()["mode"] != "idle":
            time.sleep(0.01)
        self.assertEqual(len(self.hardware.published), trajectories[0]["frame_count"] * 2)

    def test_rejects_concurrent_and_unconfirmed_replay(self):
        self.manager.start_record(name="test", sample_interval=0.02, max_duration=1)
        with self.assertRaises(RuntimeError):
            self.manager.start_record(name="again", sample_interval=0.1, max_duration=1)
        self.manager.stop_record()
        trajectory_id = self.manager.list_trajectories()[0]["id"]
        with self.assertRaises(ValueError):
            self.manager.start_replay(trajectory_id=trajectory_id)


if __name__ == "__main__":
    unittest.main()
