"""独立猜拳 Demo 的 ROS 摄像头进程管理。"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path


DEFAULT_LAUNCH_COMMAND = "source install/setup.bash && ros2 launch orbbec_camera gemini_330_series.launch.py"
DEFAULT_LAUNCH_CWD = "/home/nvidia/orbbec_camera_ros2"


class GameCameraLauncher:
    """按独立 Demo 的环境变量启动和停止 Orbbec ROS 节点。"""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None

    @property
    def started(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        """启动相机驱动；显式关闭或已在外部启动时不重复启动。"""
        enabled = os.getenv("GAME_CAMERA_AUTO_START", "true").lower() in {"1", "true", "yes", "on"}
        if not enabled:
            print("[LOG][CAMERA] 自动启动已关闭，使用已有 ROS 摄像头节点。")
            return
        command = os.getenv("GAME_CAMERA_LAUNCH_COMMAND", DEFAULT_LAUNCH_COMMAND).strip()
        cwd = os.getenv("GAME_CAMERA_LAUNCH_CWD", DEFAULT_LAUNCH_CWD).strip() or None
        if not command:
            print("[LOG][CAMERA] 启动命令为空，使用已有 ROS 摄像头节点。")
            return
        if os.name != "posix":
            print("[LOG][CAMERA] 当前系统不是 Linux，跳过 ROS 摄像头自动启动。")
            return
        try:
            self._process = subprocess.Popen(
                ["bash", "-lc", command],
                cwd=cwd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            delay = float(os.getenv("GAME_CAMERA_STARTUP_DELAY_SEC", "3.0"))
            if delay < 0:
                raise ValueError("GAME_CAMERA_STARTUP_DELAY_SEC 不能为负数")
            print(f"[LOG][CAMERA] 已启动 Orbbec 摄像头进程 PID={self._process.pid}，等待 {delay:.1f} 秒。")
            if delay:
                time.sleep(delay)
            if self._process.poll() is not None:
                raise RuntimeError(f"摄像头进程已退出，返回码={self._process.returncode}")
        except Exception as exc:
            self._process = None
            raise RuntimeError(f"摄像头启动失败（cwd={cwd!r}）：{exc}") from exc

    def stop(self) -> None:
        """只停止本 Demo 启动的进程组。"""
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=5)
            print("[LOG][CAMERA] 摄像头进程已停止。")
        except Exception as exc:
            print(f"[LOG][CAMERA] 摄像头停止失败：{exc}")
