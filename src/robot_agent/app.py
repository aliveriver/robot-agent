"""Start full robot-agent control first, then supervise optional voice and cloud readiness."""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import subprocess
import signal

from src.robot_agent.bootstrap.agent_runtime import run_agent
from src.robot_agent.bootstrap.audio_readiness import (
    require_configured_audio, ComponentUnavailable, configured_bluetooth_address,
)
from src.robot_agent.bootstrap.health import runtime_health
from src.robot_agent.bootstrap.resilience import monitor_cloud, supervise


async def voice_session(ready) -> None:
    """Wait for explicit audio before importing heavy voice/model dependencies."""
    try:
        await asyncio.to_thread(require_configured_audio)
    except ComponentUnavailable as exc:
        address = configured_bluetooth_address() if str(exc) == 'microphone_missing' else ''
        if address:
            try:
                await asyncio.to_thread(subprocess.run, ['bluetoothctl', 'connect', address],
                                        capture_output=True, timeout=8, check=False)
            except (OSError, subprocess.SubprocessError):
                pass
        await asyncio.to_thread(require_configured_audio)
    module = await asyncio.to_thread(importlib.import_module, 'src.robot_agent.bootstrap.voice_runtime')
    await module.run_voice(ready)


async def cloud_monitor() -> None:
    """Observe configured cloud transports; do not send prompts, credentials or audio."""
    from src.robot_agent.settings import settings
    await monitor_cloud({'llm': settings.llm.api_url, 'tts': settings.tts.ws_url}, runtime_health)


async def discovery_worker() -> None:
    """Retry discovery separately so a UDP port conflict cannot stop WS control."""
    from src.robot_agent.interfaces.websocket.discovery import start_discovery, stop_discovery
    async def run(ready):
        try:
            await start_discovery(ws_port=int(os.getenv('WS_PORT', '8765')))
            ready()
            await asyncio.Future()
        finally:
            await stop_discovery()
    await supervise('discovery', run, runtime_health)


async def camera_worker() -> None:
    """Preserve configured camera startup without waiting for microphone readiness."""
    from src.robot_agent.bootstrap.camera_node import CameraNodeLauncher
    camera = CameraNodeLauncher()
    try:
        await asyncio.to_thread(camera.start)
        await asyncio.Future()
    finally:
        await asyncio.to_thread(camera.stop)


async def main() -> None:
    """Run the existing control API and independently recover missing capabilities."""
    logging.basicConfig(level=logging.INFO)
    from src.robot_agent.interfaces.websocket.server import start_server, stop_server, watch_server
    loop = asyncio.get_running_loop()
    owner = asyncio.current_task()
    if os.name == 'posix':
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, owner.cancel)
    async def start():
        await start_server(host='0.0.0.0', port=int(os.getenv('WS_PORT', '8765')))
    try:
        await run_agent(start, stop_server, voice_session, runtime_health,
                        background=(cloud_monitor, discovery_worker, camera_worker, watch_server))
    except asyncio.CancelledError:
        pass


if __name__ == '__main__':
    from src.robot_agent.bootstrap.entrypoint import run_application
    run_application(main)
