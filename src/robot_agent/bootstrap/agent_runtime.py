"""Keep mandatory control independent of retryable voice and cloud tasks."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .health import RuntimeHealth
from .resilience import supervise


async def run_agent(start_control: Callable[[], Awaitable[None]],
                    stop_control: Callable[[], Awaitable[None]],
                    voice: Callable[[Callable[[], None]], Awaitable[None]],
                    health: RuntimeHealth, *, retry_delay: float = 10.0,
                    background: tuple[Callable[[], Awaitable[None]], ...] = ()) -> None:
    """Serve control before optional workers and clean all tasks on service shutdown."""
    health.set_mode('full-agent')
    health.set('control', 'starting')
    tasks: list[asyncio.Task] = []
    try:
        await start_control()
        health.set('control', 'ready')
        tasks.append(asyncio.create_task(supervise('voice', voice, health, retry_delay)))
        tasks.extend(asyncio.create_task(worker()) for worker in background)
        # A background worker unexpectedly exiting is a process failure, not a healthy daemon.
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
        raise RuntimeError('Agent worker exited unexpectedly')
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await stop_control()
        health.set('control', 'stopped')
