"""Versioned BLE messages and per-peer bounded UTF-8 line framing.

Credentials are accepted only by ``decode_request`` and excluded from repr.
``FrameDecoder.feed`` accepts ATT-sized chunks independently for each phone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import time
from typing import Callable

SERVICE_UUID = 'a3b0c100-6b8b-4c2f-9a45-7f0b9c24a001'
INFO_UUID = 'a3b0c100-6b8b-4c2f-9a45-7f0b9c24a002'
REQUEST_UUID = 'a3b0c100-6b8b-4c2f-9a45-7f0b9c24a003'
STATUS_UUID = 'a3b0c100-6b8b-4c2f-9a45-7f0b9c24a004'
MAX_FRAME = 4096


class ProtocolError(ValueError):
    """A public error code, never a raw request or operating-system exception."""


@dataclass(frozen=True)
class Request:
    """One validated operation; the password never appears in diagnostic repr."""
    id: str
    action: str
    ssid: str = ''
    password: str = field(default='', repr=False)
    hidden: bool = False


def decode_request(raw: bytes) -> Request:
    """Reject malformed, oversized or unrecognized credential requests."""
    if len(raw) > MAX_FRAME:
        raise ProtocolError('TOO_LARGE')
    try:
        data = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError):
        raise ProtocolError('BAD_PAYLOAD') from None
    if not isinstance(data, dict) or data.get('v') != 1 or isinstance(data.get('v'), bool):
        raise ProtocolError('BAD_VERSION')
    request_id = data.get('id')
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', request_id):
        raise ProtocolError('BAD_ID')
    action = data.get('action')
    if action == 'wifi.status':
        if set(data) != {'v', 'id', 'action'}:
            raise ProtocolError('BAD_PAYLOAD')
        return Request(id=request_id, action=action)
    if action != 'wifi.connect':
        raise ProtocolError('BAD_ACTION')
    if set(data) - {'v','id','action','ssid','password','hidden'}:
        raise ProtocolError('BAD_PAYLOAD')
    ssid, password, hidden = data.get('ssid'), data.get('password'), data.get('hidden', False)
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode('utf-8')) <= 32:
        raise ProtocolError('BAD_SSID')
    if any(ord(char) < 32 or ord(char) == 127 for char in ssid):
        raise ProtocolError('BAD_SSID')
    if not isinstance(password, str) or any(ord(char) < 32 or ord(char) == 127 for char in password):
        raise ProtocolError('BAD_PASSWORD')
    if not (8 <= len(password.encode('utf-8')) <= 63 or re.fullmatch('[0-9a-fA-F]{64}', password)):
        raise ProtocolError('BAD_PASSWORD')
    if not isinstance(hidden, bool):
        raise ProtocolError('BAD_PAYLOAD')
    return Request(request_id, action, ssid, password, hidden)


class FrameDecoder:
    """Bound incomplete frames by size, age and number of connected peers."""
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._buffers: dict[str, tuple[float, bytearray]] = {}

    def discard(self, peer: str) -> None:
        """Drop partial credentials immediately when their BLE peer disconnects."""
        self._buffers.pop(peer, None)

    def feed(self, peer: str, chunk: bytes) -> list[Request]:
        """Return complete validated requests; reset a damaged peer's buffer."""
        now = self._clock()
        self._buffers = {key:value for key,value in self._buffers.items() if now-value[0] < 30}
        if peer not in self._buffers and len(self._buffers) >= 32:
            raise ProtocolError('BUSY')
        started, buffer = self._buffers.get(peer, (now, bytearray()))
        buffer.extend(chunk)
        try:
            if len(buffer) > MAX_FRAME + 1 or (len(buffer) > MAX_FRAME and b'\n' not in buffer):
                raise ProtocolError('TOO_LARGE')
            requests=[]
            while b'\n' in buffer:
                line, _, rest = buffer.partition(b'\n')
                buffer = bytearray(rest)
                if line:
                    requests.append(decode_request(bytes(line)))
            if buffer:
                self._buffers[peer] = (started, buffer)
            else:
                self.discard(peer)
            return requests
        except ProtocolError:
            self.discard(peer)
            raise
