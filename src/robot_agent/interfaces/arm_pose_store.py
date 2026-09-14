"""机器人端固定双臂动作存储。"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any


class ArmPoseStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.getenv("ARM_POSE_PATH", Path.cwd() / "data" / "arm_poses.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.path.exists():
                return []
            return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, name: str, left: list[float], right: list[float]) -> dict[str, Any]:
        if not name.strip() or len(left) != 7 or len(right) != 7:
            raise ValueError("动作名称不能为空，且左右臂必须各有 7 个关节值")
        pose = {"id": uuid.uuid4().hex, "name": name.strip()[:80], "left": left, "right": right}
        with self._lock:
            poses = self.list()
            poses.append(pose)
            self.path.write_text(json.dumps(poses, ensure_ascii=False, indent=2), encoding="utf-8")
        return pose

    def get(self, pose_id: str) -> dict[str, Any]:
        for pose in self.list():
            if pose["id"] == pose_id:
                return pose
        raise KeyError(f"arm pose not found: {pose_id}")

    def delete(self, pose_id: str) -> dict[str, str]:
        with self._lock:
            poses = self.list()
            remaining = [pose for pose in poses if pose["id"] != pose_id]
            if len(remaining) == len(poses):
                raise KeyError(f"arm pose not found: {pose_id}")
            self.path.write_text(json.dumps(remaining, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"deleted": pose_id}
