"""Background camera node launcher."""

from __future__ import annotations

import os
import signal
import subprocess
import time

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.settings import settings

logger = get_logger(__name__)


class CameraNodeLauncher:
    """Start and stop the external ROS camera node when configured."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        vision_cfg = settings.vision

        if not vision_cfg.auto_start_node:
            logger.info("camera_node: auto start disabled")
            return

        if self._process is not None and self._process.poll() is None:
            logger.info("camera_node: already running", pid=self._process.pid)
            return

        command = vision_cfg.launch_command.strip()
        if not command:
            logger.warning("camera_node: auto start enabled but launch_command is empty")
            return

        if os.name != "posix":
            logger.info("camera_node: auto start skipped on non-posix host")
            return

        cwd = vision_cfg.launch_cwd.strip() or None
        try:
            self._process = subprocess.Popen(
                ["bash", "-lc", command],
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
            logger.info(
                "camera_node: started",
                pid=self._process.pid,
                cwd=cwd,
                startup_delay_sec=vision_cfg.startup_delay_sec,
            )
            if vision_cfg.startup_delay_sec > 0:
                time.sleep(vision_cfg.startup_delay_sec)
        except Exception as exc:  # noqa: BLE001
            self._process = None
            logger.warning("camera_node: start failed", error=str(exc), cwd=cwd)

    def stop(self) -> None:
        process = self._process
        if process is None:
            return

        try:
            if process.poll() is not None:
                return
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=5)
            logger.info("camera_node: stopped", pid=process.pid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("camera_node: stop failed", pid=process.pid, error=str(exc))
        finally:
            self._process = None
