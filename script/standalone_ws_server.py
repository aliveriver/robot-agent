"""
standalone_ws_server.py - 独立 WebSocket + UDP 发现测试脚本 (FastAPI 版)

不依赖 robot-agent 的其他组件，仅启动 WebSocket server 和 UDP 发现服务。

用法：
    python standalone_ws_server.py
"""

import asyncio
import json
import socket
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import uvicorn

WS_PORT = 8765
DISCOVER_PORT = 9999
DISCOVER_MAGIC = b"ROBOT_DISCOVER"
ROBOT_NAME = "天轶 2.0 Pro (测试)"

app = FastAPI()


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


# ── HTTP ping (for subnet scan fallback) ─────────────────────────

@app.get("/ping")
async def http_ping():
    return JSONResponse({"ok": True, "name": ROBOT_NAME, "ws_port": WS_PORT})


# ── WebSocket endpoint ───────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    remote = ws.client
    print(f"[WS] 客户端连接: {remote}")

    try:
        await ws.send_text(json.dumps({
            "type": "event",
            "event": "connected",
            "data": {
                "robot_name": ROBOT_NAME,
                "actions": ["ping", "wake", "sleep", "get_status", "say",
                            "move_arm", "control_hand", "reset_arms",
                            "list_gestures", "get_arm_status"],
            },
        }))

        while True:
            raw = await ws.receive_text()
            print(f"[WS] 收到: {raw}")
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"type": "result", "ok": False, "error": "invalid json"}))
                continue

            action = msg.get("action", "")
            req_id = msg.get("id", "")
            params = msg.get("params", {})

            if action == "ping":
                data = {"pong": True, "ts": time.time()}
            elif action == "get_status":
                data = {"wake_state": "awake", "uptime": 123}
            elif action == "wake":
                data = {"ok": True, "wake_state": "awake"}
            elif action == "sleep":
                data = {"ok": True, "wake_state": "sleep"}
            elif action == "say":
                text = params.get("text", "")
                print(f"[TTS] 模拟说话: {text}")
                data = {"ok": True, "spoken": text}
            elif action == "control_hand":
                gesture = params.get("gesture", "")
                side = params.get("side", "right")
                print(f"[HAND] {side} -> {gesture}")
                data = {"ok": True, "gesture": gesture, "side": side}
            elif action == "move_arm":
                side = params.get("side", "left")
                positions = params.get("positions", [])
                print(f"[ARM] {side} -> {positions}")
                data = {"ok": True, "side": side, "positions": positions}
            elif action == "reset_arms":
                print("[ARM] 复位")
                data = {"ok": True, "message": "arms reset"}
            elif action == "list_gestures":
                data = {"gestures": ["open", "close", "thumbup", "peace", "point", "ok"]}
            elif action == "get_arm_status":
                data = {"left": [0.0]*7, "right": [0.0]*7, "status": "simulated"}
            else:
                await ws.send_text(json.dumps({"type": "result", "id": req_id, "ok": False, "error": f"unknown: {action}"}))
                continue

            await ws.send_text(json.dumps({"type": "result", "id": req_id, "ok": True, "data": data}))

    except WebSocketDisconnect:
        print(f"[WS] 客户端断开: {remote}")
    except Exception as e:
        print(f"[WS] 连接异常: {remote} ({e})")


# ── UDP Discovery ────────────────────────────────────────────────

class DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data.startswith(DISCOVER_MAGIC):
            local_ip = get_local_ip()
            response = json.dumps({
                "type": "robot_announce",
                "name": ROBOT_NAME,
                "ip": local_ip,
                "ws_port": WS_PORT,
            }).encode("utf-8")
            self.transport.sendto(response, addr)
            print(f"[DISCOVERY] 收到搜索请求 from {addr}, 回复 IP={local_ip}")


# ── Main ─────────────────────────────────────────────────────────

async def main():
    local_ip = get_local_ip()
    print(f"{'='*50}")
    print(f"  机器人 WebSocket 测试服务器 (FastAPI)")
    print(f"  本机 IP: {local_ip}")
    print(f"  WebSocket: ws://{local_ip}:{WS_PORT}/ws")
    print(f"  HTTP Ping: http://{local_ip}:{WS_PORT}/ping")
    print(f"  UDP 发现: 端口 {DISCOVER_PORT}")
    print(f"{'='*50}")
    print()

    # 启动 UDP 发现
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        DiscoveryProtocol,
        local_addr=("0.0.0.0", DISCOVER_PORT),
        allow_broadcast=True,
    )
    print(f"[DISCOVERY] 监听 UDP 0.0.0.0:{DISCOVER_PORT}")

    # 启动 FastAPI (uvicorn)
    config = uvicorn.Config(app, host="0.0.0.0", port=WS_PORT, log_level="info")
    server = uvicorn.Server(config)
    print(f"[WS] 启动 FastAPI ws://0.0.0.0:{WS_PORT}/ws")
    print()
    print("等待手机连接... (Ctrl+C 退出)")
    await server.serve()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已退出")
