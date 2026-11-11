"""机器人轨迹录制、持久化与回放接口。"""

from .manager import TrajectoryManager, get_trajectory_manager

__all__ = ["TrajectoryManager", "get_trajectory_manager"]
