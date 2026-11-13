"""Per-phone status, request de-duplication and a single asynchronous Wi-Fi worker."""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading
from typing import Any

from .network import NetworkError
from .protocol import ProtocolError, Request


class ProvisioningController:
    """Serialize network changes while keeping BLE request handlers non-blocking."""
    def __init__(self, backend: Any, *, robot_id: str, name: str) -> None:
        self.backend, self.robot_id, self.name = backend, robot_id, name
        self._lock = threading.RLock()
        self._busy = False
        self._statuses: OrderedDict[str, dict] = OrderedDict()
        self._seen: OrderedDict[tuple[str,str], str] = OrderedDict()
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='wifi-provision')

    def info(self) -> dict:
        """Return public identity; no network credentials are exposed."""
        return dict(v=1,robot_id=self.robot_id,name=self.name)

    def read_status(self, peer: str) -> dict:
        """Read a copy so GATT clients cannot mutate controller state."""
        with self._lock:
            return dict(self._statuses.get(peer,dict(v=1,robot_id=self.robot_id,id='',state='idle')))

    def submit(self, peer: str, request: Request) -> None:
        """Accept one operation, or return a stable public rejection code."""
        digest = hashlib.sha256(json.dumps([request.action,request.ssid,request.password,request.hidden]).encode()).hexdigest()
        key = peer,request.id
        with self._lock:
            if key in self._seen:
                if self._seen[key] != digest:
                    raise ProtocolError('ID_REUSED')
                return
            if self._busy:
                raise ProtocolError('BUSY')
            self._busy = True
            self._seen[key] = digest
            while len(self._seen) > 128:
                self._seen.popitem(last=False)
            self._statuses[peer] = dict(v=1,robot_id=self.robot_id,id=request.id,state='connecting')
            self._statuses.move_to_end(peer)
            while len(self._statuses) > 32:
                self._statuses.popitem(last=False)
            self._worker.submit(self._run, peer, request)

    def _run(self, peer: str, request: Request) -> None:
        try:
            result = self.backend.connect(request) if request.action == 'wifi.connect' else self.backend.snapshot()
            public = {key:result[key] for key in ['ip','ws_port','agent_ready']}
            status = dict(v=1,robot_id=self.robot_id,id=request.id,state='connected',**public)
        except Exception as exc:
            known = {'AUTH_FAILED','TIMEOUT','NO_WIFI','NOT_CONNECTED','MISSING_IP','NOT_ALLOWED'}
            code = str(exc) if isinstance(exc,NetworkError) and str(exc) in known else 'CONNECT_FAILED'
            status = dict(v=1,robot_id=self.robot_id,id=request.id,state='failed',code=code)
        with self._lock:
            self._statuses[peer] = status
            self._busy = False

    def close(self) -> None:
        """Wait for the bounded network transaction before releasing the controller."""
        self._worker.shutdown(wait=True)
