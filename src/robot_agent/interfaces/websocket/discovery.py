"""
interfaces/websocket/discovery.py - UDP 局域网发现服务

机器人监听 UDP 9999 端口，收到 "ROBOT_DISCOVER" 广播后，
回复 JSON 包含自身 IP、WebSocket 端口和名称，供手机 App 自动发现。
"""

from __future__ import annotations

import asyncio
import json
import socket
from typing import Optional

from src.robot_agent.bootstrap.logging import get_logger

logger = get_logger(__name__)

DISCOVER_PORT = 9999
DISCOVER_MAGIC = b"ROBOT_DISCOVER"
ROBOT_NAME = "天轶 2.0 Pro"

_transport: Optional[asyncio.DatagramTransport] = None


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, ws_port: int) -> None:
        self.ws_port = ws_port

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if not data.startswith(DISCOVER_MAGIC):
            return

        local_ip = _get_local_ip()
        response = json.dumps({
            "type": "robot_announce",
            "name": ROBOT_NAME,
            "ip": local_ip,
            "ws_port": self.ws_port,
        }, ensure_ascii=False).encode("utf-8")

        self.transport.sendto(response, addr)
        logger.info("discovery: replied to", remote=addr, local_ip=local_ip)


def _get_local_ip() -> str:
    """获取本机局域网 IP（连接外部地址来确定出口 IP）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


async def start_discovery(ws_port: int = 8765) -> None:
    """启动 UDP 发现服务。"""
    global _transport
    loop = asyncio.get_running_loop()

    _transport, _ = await loop.create_datagram_endpoint(
        lambda: _DiscoveryProtocol(ws_port),
        local_addr=("0.0.0.0", DISCOVER_PORT),
        allow_broadcast=True,
    )
    logger.info("discovery: listening", port=DISCOVER_PORT)


async def stop_discovery() -> None:
    """停止 UDP 发现服务。"""
    global _transport
    if _transport is not None:
        _transport.close()
        _transport = None
        logger.info("discovery: stopped")
