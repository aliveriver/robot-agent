"""
interfaces/websocket/server.py - 手机 App 遥控 WebSocket 服务

在局域网内暴露 WebSocket 端口，接收来自手机 App 的控制指令，
调用已有的 tool 函数执行动作，并推送机器人状态。

协议格式（JSON）：
  请求: {"type": "command", "action": "<action_name>", "params": {...}, "id": "<req_id>"}
  响应: {"type": "result", "id": "<req_id>", "ok": true/false, "data": {...}}
  推送: {"type": "event", "event": "<event_name>", "data": {...}}
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Set

import websockets
from websockets.server import WebSocketServerProtocol

from src.robot_agent.bootstrap.logging import get_logger
from src.robot_agent.graph.state import AgentState
from src.robot_agent.runtime import runtime_session
from src.robot_agent.settings import settings

logger = get_logger(__name__)

_clients: Set[WebSocketServerProtocol] = set()
_server = None

async def _build_state() -> AgentState:
    """构造一个最小 AgentState 供 tool 函数使用。"""
    return AgentState(
        session_id="ws_remote",
        user_id="app_user",
        input_text="",
        normalized_text="",
        language=settings.lang,
        emotion="neutral",
        wake_state=runtime_session.wake_state,
        interrupt_revision=runtime_session.get_interrupt_revision(),
    )


# ── Action handlers ──────────────────────────────────────────────

async def _action_ping(_params: dict) -> dict:
    return {"pong": True, "ts": time.time()}


async def _action_get_status(_params: dict) -> dict:
    from src.robot_agent.tools.builtin.device_tools import get_robot_status
    state = await _build_state()
    return await get_robot_status(state)


async def _action_get_arm_status(_params: dict) -> dict:
    from src.robot_agent.tools.builtin.arm_tools import get_arm_status
    state = await _build_state()
    return await get_arm_status(state)


async def _action_move_arm(params: dict) -> dict:
    from src.robot_agent.tools.builtin.arm_tools import move_arm_joints
    state = await _build_state()
    return await move_arm_joints(
        state,
        side=params.get("side", "left"),
        positions=params.get("positions"),
        kp=params.get("kp", 100.0),
        kd=params.get("kd", 2.0),
    )


async def _action_control_hand(params: dict) -> dict:
    from src.robot_agent.tools.builtin.arm_tools import control_hand
    state = await _build_state()
    return await control_hand(
        state,
        side=params.get("side", "right"),
        gesture=params.get("gesture", "open"),
        angles=params.get("angles"),
    )


async def _action_reset_arms(_params: dict) -> dict:
    from src.robot_agent.tools.builtin.arm_tools import reset_arms
    state = await _build_state()
    return await reset_arms(state)


async def _action_list_gestures(_params: dict) -> dict:
    from src.robot_agent.tools.builtin.arm_tools import list_gestures
    state = await _build_state()
    return await list_gestures(state)


async def _action_say(params: dict) -> dict:
    """让机器人说一句话（通过 TTS）。"""
    text = params.get("text", "")
    if not text:
        return {"ok": False, "error": "text is required"}
    from src.robot_agent.capabilities.tts.minimax_adapter import MinimaxTTS
    tts = MinimaxTTS()
    await tts.speak(text)
    return {"ok": True, "spoken": text}


async def _action_wake(_params: dict) -> dict:
    """唤醒机器人。"""
    runtime_session.wake_state = "awake"
    return {"ok": True, "wake_state": "awake"}


async def _action_sleep(_params: dict) -> dict:
    """让机器人休眠。"""
    runtime_session.wake_state = "sleep"
    return {"ok": True, "wake_state": "sleep"}


_ACTION_HANDLERS: dict[str, Any] = {
    "ping": _action_ping,
    "get_status": _action_get_status,
    "get_arm_status": _action_get_arm_status,
    "move_arm": _action_move_arm,
    "control_hand": _action_control_hand,
    "reset_arms": _action_reset_arms,
    "list_gestures": _action_list_gestures,
    "say": _action_say,
    "wake": _action_wake,
    "sleep": _action_sleep,
}


# ── WebSocket 连接处理 ────────────────────────────────────────────

async def _handle_command(ws: WebSocketServerProtocol, msg: dict) -> None:
    """处理单条指令并回复结果。"""
    action = msg.get("action", "")
    params = msg.get("params", {})
    req_id = msg.get("id", "")

    handler = _ACTION_HANDLERS.get(action)
    if handler is None:
        await _send(ws, {
            "type": "result",
            "id": req_id,
            "ok": False,
            "error": f"unknown action: {action}",
        })
        return

    try:
        result = await handler(params)
        await _send(ws, {
            "type": "result",
            "id": req_id,
            "ok": True,
            "data": result,
        })
    except Exception as exc:
        logger.exception("ws: command failed", action=action, error=str(exc))
        await _send(ws, {
            "type": "result",
            "id": req_id,
            "ok": False,
            "error": str(exc),
        })


async def _send(ws: WebSocketServerProtocol, data: dict) -> None:
    try:
        await ws.send(json.dumps(data, ensure_ascii=False))
    except websockets.exceptions.ConnectionClosed:
        pass


async def broadcast(event: str, data: dict) -> None:
    """向所有已连接客户端广播事件。"""
    if not _clients:
        return
    msg = json.dumps({"type": "event", "event": event, "data": data}, ensure_ascii=False)
    await asyncio.gather(
        *[client.send(msg) for client in _clients],
        return_exceptions=True,
    )


async def _ws_handler(ws: WebSocketServerProtocol) -> None:
    """单个 WebSocket 连接的生命周期处理。"""
    _clients.add(ws)
    remote = ws.remote_address
    logger.info("ws: client connected", remote=str(remote))

    try:
        await _send(ws, {
            "type": "event",
            "event": "connected",
            "data": {"robot_name": "天轶 2.0 Pro", "actions": list(_ACTION_HANDLERS.keys())},
        })

        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send(ws, {"type": "result", "ok": False, "error": "invalid json"})
                continue

            msg_type = msg.get("type", "command")
            if msg_type == "command":
                await _handle_command(ws, msg)
            elif msg_type == "ping":
                await _send(ws, {"type": "pong", "ts": time.time()})
            else:
                await _send(ws, {"type": "result", "ok": False, "error": f"unknown type: {msg_type}"})
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        _clients.discard(ws)
        logger.info("ws: client disconnected", remote=str(remote))


async def start_server(host: str = "0.0.0.0", port: int = 8765) -> None:
    """启动 WebSocket 服务器（非阻塞，作为后台任务运行）。"""
    global _server
    _server = await websockets.serve(_ws_handler, host, port)
    logger.info("ws: server started", host=host, port=port)


async def stop_server() -> None:
    """停止 WebSocket 服务器。"""
    global _server
    if _server is not None:
        _server.close()
        await _server.wait_closed()
        _server = None
        logger.info("ws: server stopped")
