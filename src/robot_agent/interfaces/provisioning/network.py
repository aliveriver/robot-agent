"""Wi-Fi transaction and rollback policy, independent of the D-Bus transport."""

from __future__ import annotations

import ipaddress
import json
from typing import Any, Callable
import urllib.request

from .protocol import Request


class NetworkError(RuntimeError):
    """Public network failure code that contains no credentials."""


def valid_wifi_address(value: str) -> str:
    """Accept only usable IPv4 addresses supplied by the selected Wi-Fi device."""
    try:
        address = ipaddress.IPv4Address(value)
    except (ValueError, TypeError):
        raise NetworkError('MISSING_IP') from None
    if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local or str(address) == '255.255.255.255':
        raise NetworkError('MISSING_IP')
    return str(address)


def agent_ready(port: int) -> bool:
    """Probe the local control service without consulting proxy environment settings."""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/ping', timeout=1) as response:
            return json.load(response).get('ok') is True
    except Exception:
        return False


class NetworkConnector:
    """Activate one new profile, rolling back only Wi-Fi if activation fails."""
    def __init__(self, driver: Any, *, port: int = 8765, timeout: float = 65,
                 probe: Callable[[int], bool] = agent_ready) -> None:
        if not 1 <= port <= 65535:
            raise ValueError('invalid control port')
        self.driver, self.port, self.timeout, self.probe = driver, port, timeout, probe

    def _result(self, address: str) -> dict:
        return dict(ip=valid_wifi_address(address), ws_port=self.port, agent_ready=self.probe(self.port))

    def snapshot(self) -> dict:
        """Read the current Wi-Fi address even when no new provisioning is needed."""
        return self._result(self.driver.current_ipv4())

    def connect(self, request: Request) -> dict:
        """Keep existing profiles intact until the new hotspot has a usable address."""
        previous = self.driver.current_profile()
        candidate = None
        try:
            candidate = self.driver.create_profile(request)
            self.driver.activate(candidate)
            result = self._result(self.driver.wait_ipv4(candidate, self.timeout))
        except Exception:
            if candidate is not None:
                try:
                    self.driver.remove_profile(candidate)
                except Exception:
                    pass
                if previous:
                    try:
                        self.driver.activate(previous)
                    except Exception:
                        pass
            raise
        # Failure to remove an obsolete managed profile must not undo a working network.
        try:
            self.driver.prune_profiles(request, candidate)
        except Exception:
            pass
        return result
