"""Manage the game camera: reuse an existing driver, verify frames, clean up owned processes."""

from __future__ import annotations

import math
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

DEFAULT_LAUNCH_COMMAND = "source install/setup.bash && ros2 launch orbbec_camera gemini_330_series.launch.py"
DEFAULT_LAUNCH_CWD = "/home/nvidia/orbbec_camera_ros2"


class GameCameraLauncher:
    """Start at most one Orbbec driver and stop only the process group we own."""

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._log = None

    @property
    def started(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @staticmethod
    def _existing_driver_pids() -> list[int]:
        """Inspect local launch commands even before DDS discovery completes."""
        pids = []
        for entry in Path('/proc').glob('[0-9]*'):
            try:
                args = (entry / 'cmdline').read_bytes().split(b'\0')
                if any(Path(os.fsdecode(arg)).name == 'ros2' for arg in args if arg):
                    if b'launch' in args and b'orbbec_camera' in args:
                        pids.append(int(entry.name))
            except (OSError, ValueError):
                continue
        return sorted(pids)

    def start(self, ros_node=None) -> None:
        """Reuse discovered cameras; refuse to compete with an undiscovered driver."""
        enabled = os.getenv('GAME_CAMERA_AUTO_START', 'true').lower() in {'1', 'true', 'yes', 'on'}
        if not enabled:
            print('[LOG][CAMERA] 自动启动已关闭，使用已有 ROS 摄像头节点。')
            return
        command = os.getenv('GAME_CAMERA_LAUNCH_COMMAND', DEFAULT_LAUNCH_COMMAND).strip()
        cwd = os.getenv('GAME_CAMERA_LAUNCH_CWD', DEFAULT_LAUNCH_CWD).strip() or None
        if not command or os.name != 'posix':
            return
        # Validate before spawning, so invalid settings cannot leave an orphan driver.
        delay = float(os.getenv('GAME_CAMERA_STARTUP_DELAY_SEC', '3.0'))
        timeout = float(os.getenv('GAME_CAMERA_READY_TIMEOUT_SEC', '15.0'))
        if not math.isfinite(delay) or delay < 0 or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('相机启动延时必须有限且非负，就绪超时必须有限且大于零')
        if self.started:
            return
        if ros_node is not None and self._camera_node_exists(ros_node):
            print('[LOG][CAMERA] 复用现有摄像头节点，检查图像。')
            self._wait_for_image(ros_node, timeout)
            return
        existing = self._existing_driver_pids()
        if existing:
            raise RuntimeError(
                f'已有 Orbbec 驱动 PID={existing}，但当前 ROS 图未发现相机；'
                '为避免 USB 设备争用，取消重复启动。请检查 ROS_DOMAIN_ID 和已有驱动状态。'
            )
        try:
            self._log = tempfile.NamedTemporaryFile(mode='w+b', prefix='robot-game-camera-', suffix='.log', delete=False)
            print(f'[LOG][CAMERA] 驱动日志: {self._log.name}')
            self._process = subprocess.Popen(
                ['bash', '-lc', command], cwd=cwd, start_new_session=True,
                stdout=self._log, stderr=subprocess.STDOUT,
            )
            print(f'[LOG][CAMERA] 已启动 Orbbec PID={self._process.pid}，等待图像就绪。')
            if ros_node is not None:
                self._wait_for_image(ros_node, timeout)
            elif delay:
                time.sleep(delay)
            if self._process.poll() is not None:
                raise RuntimeError(f'摄像头进程已退出，返回码={self._process.returncode}')
        except BaseException:
            # SIGINT and initialization errors must also release the USB device.
            if self._log is not None:
                self._log.seek(0, os.SEEK_END)
                size = self._log.tell()
                self._log.seek(max(0, size - 3000))
                print(self._log.read().decode('utf-8', errors='replace'))
            self.stop()
            raise

    def _wait_for_image(self, ros_node, timeout: float) -> None:
        """A live launch process is insufficient: require a fresh ROS image."""
        import rclpy
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import Image

        received = False

        def on_image(msg):
            nonlocal received
            received = msg.width > 0 and msg.height > 0 and len(msg.data) > 0

        topic = os.getenv('VISION_CAMERA_TOPIC', '/camera/color/image_raw')
        subscription = ros_node.create_subscription(Image, topic, on_image, qos_profile_sensor_data)
        try:
            deadline = time.monotonic() + timeout
            while rclpy.ok() and time.monotonic() < deadline:
                if self._process is not None and self._process.poll() is not None:
                    raise RuntimeError(f'相机驱动提前退出: {self._process.returncode}')
                rclpy.spin_once(ros_node, timeout_sec=0.1)
                if received:
                    print(f'[LOG][CAMERA] 已收到有效图像: {topic}')
                    return
            raise RuntimeError(f'{timeout:.1f} 秒内未收到 {topic} 图像；请检查设备占用和驱动日志。')
        finally:
            ros_node.destroy_subscription(subscription)

    @staticmethod
    def _camera_node_exists(ros_node, timeout: float = 2.0) -> bool:
        """Allow DDS discovery to complete before deciding to launch a driver."""
        import rclpy

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rclpy.spin_once(ros_node, timeout_sec=0.05)
            if 'camera' in set(ros_node.get_node_names()):
                return True
        return False

    def stop(self) -> None:
        """Release our entire process group, including children of an exited launch."""
        process = self._process
        self._process = None
        try:
            if process is not None:
                # start_new_session=True makes the launch PID the process group ID.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)
                # The launch parent may exit while a container ignores SIGTERM.
                # Reap remaining members of our own session, never external drivers.
                try:
                    os.killpg(process.pid, 0)
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                print('[LOG][CAMERA] 本脚本启动的摄像头进程已停止。')
        finally:
            if self._log is not None:
                self._log.close()
                self._log = None
