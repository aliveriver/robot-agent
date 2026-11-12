"""Publish process-local component readiness without exposing device secrets."""
from __future__ import annotations

from copy import deepcopy
from threading import Lock
import time


class RuntimeHealth:
    """Share small immutable snapshots between workers and HTTP handlers."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._mode = 'control-only'
        self._started = time.monotonic()
        self._components: dict[str, dict] = {}

    def set_mode(self, mode: str) -> None:
        """Distinguish the full Agent from a temporary standalone control server."""
        with self._lock:
            self._mode = mode

    def set(self, component: str, state: str, reason: str = '') -> None:
        """Record stable reason codes, never raw exceptions or endpoint credentials."""
        with self._lock:
            previous = self._components.get(component, {})
            if previous.get('state') == state and previous.get('reason') == reason:
                return
            self._components[component] = {
                'state': state, 'reason': reason, 'changed_at': time.time(),
            }

    def snapshot(self) -> dict:
        """Return a copy so readers cannot change running state."""
        with self._lock:
            return {'mode': self._mode, 'uptime_seconds': round(time.monotonic() - self._started, 1),
                    'components': deepcopy(self._components)}


runtime_health = RuntimeHealth()
