"""
interfaces/websocket/server.py - 手机 App 遥控 WebSocket 服务 (FastAPI 版)

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
import logging
import time
from typing import Any, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import uvicorn

from src.robot_agent.graph.state import AgentState
from src.robot_agent.runtime import runtime_session


class _ProtocolLogger:
    """保持结构化日志调用形式，同时允许通信服务独立启动。"""

    def __init__(self) -> None:
        self._logger = logging.getLogger(__name__)

    def _write(self, level: str, message: str, **fields: Any) -> None:
        suffix = " ".join(f"{key}={value!r}" for key, value in fields.items())
        getattr(self._logger, level)(f"{message} {suffix}".rstrip())

    def info(self, message: str, **fields: Any) -> None:
        self._write("info", message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._write("warning", message, **fields)

    def debug(self, message: str, **fields: Any) -> None:
        self._write("debug", message, **fields)

    def exception(self, message: str, **fields: Any) -> None:
        suffix = " ".join(f"{key}={value!r}" for key, value in fields.items())
        self._logger.exception(f"{message} {suffix}".rstrip())


logger = _ProtocolLogger()

app = FastAPI(title="Robot Remote Control")
_clients: Set[WebSocket] = set()
_server_task: asyncio.Task | None = None


async def _build_state() -> AgentState:
    from src.robot_agent.settings import settings

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
    return await move_arm_joints(state, side=params.get("side", "left"), positions=params.get("positions"))


async def _action_move_both_arms(params: dict) -> dict:
    from src.robot_agent.tools.builtin.arm_tools import move_both_arms
    state = await _build_state()
    return await move_both_arms(state, left_positions=params.get("left_positions", []), right_positions=params.get("right_positions", []))


async def _action_control_hand(params: dict) -> dict:
    from src.robot_agent.tools.builtin.arm_tools import control_hand
    state = await _build_state()
    return await control_hand(state, side=params.get("side", "right"), gesture=params.get("gesture", "open"), angles=params.get("angles"))


async def _action_control_both_hands(params: dict) -> dict:
    from src.robot_agent.tools.builtin.arm_tools import control_both_hands
    state = await _build_state()
    return await control_both_hands(state, left_gesture="custom", right_gesture="custom", left_angles=params.get("left_angles"), right_angles=params.get("right_angles"))


_arm_pose_store_instance = None


def _arm_pose_store():
    global _arm_pose_store_instance
    if _arm_pose_store_instance is None:
        from src.robot_agent.interfaces.arm_pose_store import ArmPoseStore
        _arm_pose_store_instance = ArmPoseStore()
    return _arm_pose_store_instance


async def _action_arm_pose_list(_params: dict) -> dict:
    return {"poses": _arm_pose_store().list()}


async def _action_arm_pose_save(params: dict) -> dict:
    joints = await asyncio.to_thread(_trajectory_manager().current_joint_state)
    return await asyncio.to_thread(
        _arm_pose_store().save,
        params.get("name", ""),
        joints["left"],
        joints["right"],
        joints["left_hand"],
        joints["right_hand"],
    )


async def _action_joint_state_read(_params: dict) -> dict:
    return await asyncio.to_thread(_trajectory_manager().current_joint_state)


async def _action_arm_tension(params: dict) -> dict:
    return await asyncio.to_thread(
        _trajectory_manager().set_arm_tension,
        params.get("side", ""),
        bool(params.get("tight", False)),
    )


async def _action_arm_pose_delete(params: dict) -> dict:
    return await asyncio.to_thread(_arm_pose_store().delete, params.get("pose_id", ""))


async def _action_arm_pose_execute(params: dict) -> dict:
    if not params.get("safety_confirmed", False):
        raise ValueError("执行固定动作前必须确认周围安全")
    pose = _arm_pose_store().get(params.get("pose_id", ""))
    return await asyncio.to_thread(_trajectory_manager().hold_fixed_pose, pose)


async def _action_reset_arms(_params: dict) -> dict:
    from src.robot_agent.tools.builtin.arm_tools import reset_arms
    state = await _build_state()
    return await reset_arms(state)


async def _action_list_gestures(_params: dict) -> dict:
    from src.robot_agent.tools.builtin.arm_tools import list_gestures
    state = await _build_state()
    return await list_gestures(state)


async def _action_say(params: dict) -> dict:
    text = params.get("text", "")
    if not text:
        return {"ok": False, "error": "text is required"}
    from src.robot_agent.capabilities.tts.minimax_adapter import MinimaxTTS
    tts = MinimaxTTS()
    await tts.speak(text)
    return {"ok": True, "spoken": text}


async def _action_wake(_params: dict) -> dict:
    runtime_session.wake_state = "awake"
    return {"ok": True, "wake_state": "awake"}


async def _action_sleep(_params: dict) -> dict:
    runtime_session.wake_state = "sleep"
    return {"ok": True, "wake_state": "sleep"}


def _trajectory_manager():
    from src.robot_agent.interfaces.trajectory import get_trajectory_manager
    return get_trajectory_manager()


async def _action_trajectory_status(_params: dict) -> dict:
    return _trajectory_manager().status()


async def _action_trajectory_list(_params: dict) -> dict:
    return {"trajectories": _trajectory_manager().list_trajectories()}


async def _action_trajectory_delete(params: dict) -> dict:
    return await asyncio.to_thread(
        _trajectory_manager().delete_trajectory,
        params.get("trajectory_id", ""),
    )


async def _action_trajectory_record_start(params: dict) -> dict:
    return await asyncio.to_thread(
        _trajectory_manager().start_record,
        name=params.get("name", "未命名轨迹"),
        sample_interval=params.get("sample_interval", 0.01),
        max_duration=params.get("max_duration", 60.0),
    )


async def _action_trajectory_record_stop(_params: dict) -> dict:
    return await asyncio.to_thread(_trajectory_manager().stop_record)


async def _action_trajectory_replay_start(params: dict) -> dict:
    return await asyncio.to_thread(
        _trajectory_manager().start_replay,
        trajectory_id=params.get("trajectory_id", ""),
        speed_scale=params.get("speed_scale", 1.0),
        smoothing=params.get("smoothing", 0.0),
        repeat_count=params.get("repeat_count", 1),
        safety_confirmed=params.get("safety_confirmed", False),
    )


async def _action_trajectory_replay_stop(_params: dict) -> dict:
    return await asyncio.to_thread(_trajectory_manager().stop_replay)


_ACTION_HANDLERS: dict[str, Any] = {
    "ping": _action_ping,
    "get_status": _action_get_status,
    "get_arm_status": _action_get_arm_status,
    "move_arm": _action_move_arm,
    "move_both_arms": _action_move_both_arms,
    "control_hand": _action_control_hand,
    "control_both_hands": _action_control_both_hands,
    "arm_pose_list": _action_arm_pose_list,
    "arm_pose_save": _action_arm_pose_save,
    "joint_state_read": _action_joint_state_read,
    "arm_tension": _action_arm_tension,
    "arm_pose_delete": _action_arm_pose_delete,
    "arm_pose_execute": _action_arm_pose_execute,
    "reset_arms": _action_reset_arms,
    "list_gestures": _action_list_gestures,
    "say": _action_say,
    "wake": _action_wake,
    "sleep": _action_sleep,
    "trajectory_status": _action_trajectory_status,
    "trajectory_list": _action_trajectory_list,
    "trajectory_delete": _action_trajectory_delete,
    "trajectory_record_start": _action_trajectory_record_start,
    "trajectory_record_stop": _action_trajectory_record_stop,
    "trajectory_replay_start": _action_trajectory_replay_start,
    "trajectory_replay_stop": _action_trajectory_replay_stop,
}


# ── HTTP endpoints ───────────────────────────────────────────────

@app.get("/ping")
async def http_ping():
    return JSONResponse({"ok": True, "name": "天轶 2.0 Pro", "ws_port": 8765})


# ── WebSocket endpoint ───────────────────────────────────────────

async def _send(ws: WebSocket, data: dict) -> None:
    try:
        await ws.send_text(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)
    remote = ws.client
    logger.info("ws: client connected", remote=str(remote))

    try:
        await _send(ws, {
            "type": "event",
            "event": "connected",
            "data": {"robot_name": "天轶 2.0 Pro", "actions": list(_ACTION_HANDLERS.keys())},
        })

        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send(ws, {"type": "result", "ok": False, "error": "invalid json"})
                continue

            msg_type = msg.get("type", "command")
            if msg_type == "command":
                action = msg.get("action", "")
                params = msg.get("params", {})
                req_id = msg.get("id", "")

                handler = _ACTION_HANDLERS.get(action)
                if handler is None:
                    await _send(ws, {"type": "result", "id": req_id, "ok": False, "error": f"unknown action: {action}"})
                    continue

                try:
                    result = await handler(params)
                    # 工具层会用 {ok: false, error: ...} 表示硬件未就绪。
                    # 不能再包成外层 ok=true，否则 App 会误报“执行成功”。
                    tool_ok = not isinstance(result, dict) or result.get("ok", True) is not False
                    payload = {"type": "result", "id": req_id, "ok": tool_ok, "data": result}
                    if not tool_ok:
                        payload["error"] = result.get("error") or result.get("message") or "硬件指令执行失败"
                    await _send(ws, payload)
                except Exception as exc:
                    logger.exception("ws: command failed", action=action, error=str(exc))
                    await _send(ws, {"type": "result", "id": req_id, "ok": False, "error": str(exc)})
            elif msg_type == "ping":
                await _send(ws, {"type": "pong", "ts": time.time()})
            else:
                await _send(ws, {"type": "result", "ok": False, "error": f"unknown type: {msg_type}"})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("ws: connection error", error=str(exc))
    finally:
        _clients.discard(ws)
        if not _clients:
            try:
                await asyncio.to_thread(_trajectory_manager().stop_active)
            except Exception as exc:
                logger.warning("ws: failed to stop active trajectory", error=str(exc))
        logger.info("ws: client disconnected", remote=str(remote))


async def broadcast(event: str, data: dict) -> None:
    if not _clients:
        return
    msg = json.dumps({"type": "event", "event": event, "data": data}, ensure_ascii=False)
    for client in list(_clients):
        try:
            await client.send_text(msg)
        except Exception:
            _clients.discard(client)


# ── Server lifecycle ─────────────────────────────────────────────

async def start_server(host: str = "0.0.0.0", port: int = 8765) -> None:
    global _server_task
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    _server_task = asyncio.create_task(server.serve())
    logger.info("ws: FastAPI server started", host=host, port=port)


async def stop_server() -> None:
    global _server_task
    if _server_task is not None:
        _server_task.cancel()
        try:
            await _server_task
        except asyncio.CancelledError:
            pass
        _server_task = None
        logger.info("ws: server stopped")
