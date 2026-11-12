"""Supervise optional capabilities and check cloud transport without blocking control."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from .audio_readiness import ComponentUnavailable
from .health import RuntimeHealth

logger = logging.getLogger(__name__)


async def supervise(component: str, run: Callable[[Callable[[], None]], Awaitable[None]],
                    health: RuntimeHealth, retry_delay: float = 10.0) -> None:
    """Retry a cleaned-up optional session until shutdown, keeping core services alive."""
    try:
        while True:
            health.set(component, 'starting')
            try:
                await run(lambda: health.set(component, 'ready'))
                reason = 'session_ended'
            except ComponentUnavailable as exc:
                reason = str(exc)
            except Exception as exc:
                # Endpoint URLs and exception messages can contain API credentials.
                reason = 'initialization_failed'
                logger.warning('%s initialization failed (%s); retrying', component, type(exc).__name__)
            health.set(component, 'waiting', reason)
            await asyncio.sleep(retry_delay)
    finally:
        health.set(component, 'stopped')


async def tcp_reachable(url: str, timeout: float = 3.0) -> bool:
    """Check DNS/TCP reachability only: this does not claim API authentication works."""
    writer = None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https', 'ws', 'wss') or not parsed.hostname:
            return False
        port = parsed.port or (443 if parsed.scheme in ('https', 'wss') else 80)
        _, writer = await asyncio.wait_for(asyncio.open_connection(parsed.hostname, port), timeout)
        return True
    except (OSError, ValueError, asyncio.TimeoutError):
        return False
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()


async def monitor_cloud(urls: dict[str, str], health: RuntimeHealth,
                        probe: Callable[[str], Awaitable[bool]] = tcp_reachable,
                        interval: float = 15.0) -> None:
    """Refresh per-service transport state; never queue or replay failed commands."""
    async def check(name: str, url: str) -> None:
        try:
            reachable = await probe(url)
        except Exception:
            reachable = False
        health.set('cloud_' + name, 'ready' if reachable else 'waiting',
                   'transport_reachable' if reachable else 'transport_unreachable')
    while True:
        await asyncio.gather(*(check(name, url) for name, url in urls.items()))
        await asyncio.sleep(interval)
