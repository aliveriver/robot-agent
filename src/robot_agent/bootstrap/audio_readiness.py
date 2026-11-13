"""Require configured PulseAudio peripherals instead of capturing a fallback mic."""
from __future__ import annotations

import json
import os
import subprocess
import re
from collections.abc import Callable


class ComponentUnavailable(RuntimeError):
    """Carry an explicit public reason code for a retryable missing component."""


def pulse_names(kind: str) -> list[str]:
    """Query device names without changing pairing or selecting an audio source."""
    try:
        result = subprocess.run(['pactl', '--format=json', 'list', kind],
                                capture_output=True, text=True, timeout=5, check=True)
        return [item['name'] for item in json.loads(result.stdout)]
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        raise ComponentUnavailable('audio_server_unavailable') from None


def require_pulse_devices(source: str, sink: str,
                          query: Callable[[str], list[str]] = pulse_names) -> None:
    """Reject absent explicit devices; never replace DJI with an unrelated input."""
    if source and source not in query('sources'):
        raise ComponentUnavailable('microphone_missing')
    if sink and sink not in query('sinks'):
        raise ComponentUnavailable('speaker_missing')


def require_configured_audio() -> None:
    """Apply the deployment's explicit source and sink availability requirements."""
    if os.name == 'posix':
        if not os.getenv('PULSE_DEFAULT_SOURCE') or not os.getenv('PULSE_DEFAULT_SINK'):
            raise ComponentUnavailable('audio_not_configured')
        require_pulse_devices(os.getenv('PULSE_DEFAULT_SOURCE', ''),
                              os.getenv('PULSE_DEFAULT_SINK', ''))


def select_configured_audio() -> None:
    """Verify Pulse selection so a failed default change cannot silently select USB input."""
    require_configured_audio()
    if os.name != 'posix':
        return
    try:
        for kind, key in (('source', 'PULSE_DEFAULT_SOURCE'), ('sink', 'PULSE_DEFAULT_SINK')):
            name = os.environ[key]
            subprocess.run(['pactl', 'set-default-' + kind, name], capture_output=True,
                           timeout=5, check=True)
            result = subprocess.run(['pactl', 'get-default-' + kind], capture_output=True,
                                    text=True, timeout=5, check=True)
            if result.stdout.strip() != name:
                raise ComponentUnavailable('audio_selection_failed')
    except (OSError, subprocess.SubprocessError):
        raise ComponentUnavailable('audio_selection_failed') from None


def configured_bluetooth_address() -> str:
    """Resolve the explicit deployment address or its configured BlueZ source name."""
    address = os.getenv('AGENT_MIC_BLUETOOTH_ADDRESS', '')
    if not address:
        match = re.fullmatch(r'bluez_source\.([0-9a-fA-F_]{17})\..+',
                             os.getenv('PULSE_DEFAULT_SOURCE', ''))
        address = match[1].replace('_', ':') if match else ''
    return address if re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', address) else ''
