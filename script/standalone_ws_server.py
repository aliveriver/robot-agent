"""
standalone_ws_server.py - 独立 WebSocket + UDP 发现测试脚本

不依赖 robot-agent 的其他组件（ROS2、ASR、TTS 等），
仅启动 WebSocket server 和 UDP 发现服务，用于验证网络连通性。

用法：
    python standalone_ws_server.py

启动后会打印本机 IP，手机 App 可以搜索或手动连接。
"""

import asyncio
import json
import socket
import time

WS_PORT = 8765
DISCOVER_PORT = 9999
DISCOVER_MAGIC = b"ROBOT_DISCOVER"
ROBOT_NAME = "天轶 2.0 Pro (测试)"


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


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


# ── WebSocket Server ─────────────────────────────────────────────

async def ws_handler(websocket):
    remote = websocket.remote_address
    print(f"[WS] 客户端连接: {remote}")

    # 发送欢迎消息
    await websocket.send(json.dumps({
        "type": "event",
        "event": "connected",
        "data": {
            "robot_name": ROBOT_NAME,
            "actions": ["ping", "wake", "sleep", "get_status", "say",
                        "move_arm", "control_hand", "reset_arms",
                        "list_gestures", "get_arm_status"],
        },
    }))

    try:
        async for raw in websocket:
            print(f"[WS] 收到: {raw}")
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"type": "result", "ok": False, "error": "invalid json"}))
                continue

            action = msg.get("action", "")
            req_id = msg.get("id", "")

            # 简单模拟响应
            if action == "ping":
                data = {"pong": True, "ts": time.time()}
            elif action == "get_status":
                data = {"wake_state": "awake", "uptime": 123}
            elif action == "wake":
                data = {"ok": True, "wake_state": "awake"}
            elif action == "sleep":
                data = {"ok": True, "wake_state": "sleep"}
            elif action == "say":
                text = msg.get("params", {}).get("text", "")
                print(f"[TTS] 模拟说话: {text}")
                data = {"ok": True, "spoken": text}
            elif action == "control_hand":
                gesture = msg.get("params", {}).get("gesture", "")
                side = msg.get("params", {}).get("side", "right")
                print(f"[HAND] {side} -> {gesture}")
                data = {"ok": True, "gesture": gesture, "side": side}
            elif action == "move_arm":
                side = msg.get("params", {}).get("side", "left")
                positions = msg.get("params", {}).get("positions", [])
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
                await websocket.send(json.dumps({
                    "type": "result", "id": req_id, "ok": False,
                    "error": f"unknown action: {action}",
                }))
                continue

            await websocket.send(json.dumps({
                "type": "result", "id": req_id, "ok": True, "data": data,
            }))

    except Exception as e:
        print(f"[WS] 连接断开: {remote} ({e})")


async def main():
    import websockets

    local_ip = get_local_ip()
    print(f"{'='*50}")
    print(f"  机器人 WebSocket 测试服务器")
    print(f"  本机 IP: {local_ip}")
    print(f"  WebSocket: ws://{local_ip}:{WS_PORT}")
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

    # 启动 WebSocket
    async with websockets.serve(ws_handler, "0.0.0.0", WS_PORT):
        print(f"[WS] 监听 ws://0.0.0.0:{WS_PORT}")
        print()
        print("等待手机连接... (Ctrl+C 退出)")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已退出")
