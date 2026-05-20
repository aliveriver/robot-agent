"""
server.py - FastAPI 主入口

提供 REST API（轨迹 CRUD）+ WebSocket（实时状态推送与命令控制）。
"""

import asyncio
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from .db import init_db, get_db
from .ros_bridge import create_bridge, ALL_ARM_IDS, LEFT_ARM_IDS, RIGHT_ARM_IDS, JOINT_LABELS
from .recorder import Recorder
from .player import Player

app = FastAPI(title="轨迹管理器")
STATIC_DIR = Path(__file__).parent / "static"

bridge = None
recorder = None
player = None
ws_clients: list[WebSocket] = []


# ── 生命周期 ─────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global bridge, recorder, player
    await init_db()
    bridge = create_bridge()
    recorder = Recorder(bridge)
    player = Player(bridge)
    asyncio.ensure_future(status_broadcast_loop())


@app.on_event("shutdown")
async def shutdown():
    if bridge:
        bridge.destroy()


async def status_broadcast_loop():
    """10Hz 向所有 WebSocket 客户端推送状态。"""
    was_recording = False
    was_playing = False
    while True:
        if ws_clients and bridge:
            snapshot = bridge.get_snapshot()
            msg = json.dumps({
                "type": "status",
                "data": {
                    **snapshot.to_dict(),
                    "mode": bridge.mode,
                    "joint_modes": bridge.joint_modes,
                    "current_config": bridge.current_config,
                    "body": bridge.get_body_status(),
                },
            })
            dead = []
            for ws in ws_clients:
                try:
                    await ws.send_text(msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                ws_clients.remove(ws)

            is_recording = recorder and recorder.recording
            if is_recording:
                rec_msg = json.dumps({
                    "type": "record_status",
                    "recording": True,
                    "frames": len(recorder.frames),
                    "elapsed_sec": round(recorder.elapsed_sec, 1),
                })
                await _broadcast(rec_msg)
            elif was_recording:
                await _broadcast(json.dumps({"type": "record_status", "recording": False, "frames": 0, "elapsed_sec": 0}))
            was_recording = is_recording

            is_playing = player and player.playing
            if is_playing:
                play_msg = json.dumps({
                    "type": "play_status",
                    "playing": True,
                    "paused": bool(player.paused),
                    "progress": round(player.progress, 3),
                    "current_loop": player.current_loop,
                    "total_loops": player.total_loops,
                })
                await _broadcast(play_msg)
            elif was_playing:
                await _broadcast(json.dumps({"type": "play_status", "playing": False, "paused": False, "progress": 1.0}))
            was_playing = is_playing

        await asyncio.sleep(0.1)


async def _broadcast(msg: str):
    for ws in ws_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            pass


# ── REST API ─────────────────────────────────────────────────────

@app.get("/api/trajectories")
async def list_trajectories():
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, name, description, detailed_description, sample_interval_ms, total_frames, duration_sec, created_at FROM trajectories ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "name": r[1], "description": r[2],
                "detailed_description": r[3],
                "sample_interval_ms": r[4], "total_frames": r[5],
                "duration_sec": r[6], "created_at": r[7],
            }
            for r in rows
        ]
    finally:
        await db.close()


@app.get("/api/trajectories/{tid}")
async def get_trajectory(tid: int):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, name, description, detailed_description, sample_interval_ms, total_frames, duration_sec, created_at, updated_at FROM trajectories WHERE id = ?",
            (tid,),
        )
        row = await cursor.fetchone()
        if not row:
            return JSONResponse({"error": "not found"}, status_code=404)
        cursor2 = await db.execute(
            "SELECT frame_index, timestamp_ms, left_arm, right_arm, left_hand, right_hand FROM trajectory_frames WHERE trajectory_id = ? ORDER BY frame_index",
            (tid,),
        )
        frames = await cursor2.fetchall()
        return {
            "id": row[0], "name": row[1], "description": row[2],
            "detailed_description": row[3],
            "sample_interval_ms": row[4], "total_frames": row[5],
            "duration_sec": row[6], "created_at": row[7], "updated_at": row[8],
            "frames": [
                {
                    "frame_index": f[0], "timestamp_ms": f[1],
                    "left_arm": json.loads(f[2]), "right_arm": json.loads(f[3]),
                    "left_hand": json.loads(f[4]), "right_hand": json.loads(f[5]),
                }
                for f in frames
            ],
        }
    finally:
        await db.close()


@app.put("/api/trajectories/{tid}")
async def update_trajectory(tid: int, body: dict):
    db = await get_db()
    try:
        name = body.get("name")
        description = body.get("description")
        detailed_description = body.get("detailed_description")
        if name:
            await db.execute("UPDATE trajectories SET name = ?, updated_at = datetime('now') WHERE id = ?", (name, tid))
        if description is not None:
            await db.execute("UPDATE trajectories SET description = ?, updated_at = datetime('now') WHERE id = ?", (description, tid))
        if detailed_description is not None:
            await db.execute(
                "UPDATE trajectories SET detailed_description = ?, updated_at = datetime('now') WHERE id = ?",
                (detailed_description, tid),
            )
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@app.delete("/api/trajectories/{tid}")
async def delete_trajectory(tid: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM trajectories WHERE id = ?", (tid,))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@app.get("/api/trajectories/{tid}/export")
async def export_trajectory(tid: int):
    """导出轨迹为 JSON 文件到 ./output 目录。"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, name, description, detailed_description, sample_interval_ms, total_frames, duration_sec, created_at, updated_at FROM trajectories WHERE id = ?",
            (tid,),
        )
        row = await cursor.fetchone()
        if not row:
            return JSONResponse({"error": "not found"}, status_code=404)
        
        cursor2 = await db.execute(
            "SELECT frame_index, timestamp_ms, left_arm, right_arm, left_hand, right_hand FROM trajectory_frames WHERE trajectory_id = ? ORDER BY frame_index",
            (tid,),
        )
        frames = await cursor2.fetchall()
        
        # 组装导出数据
        export_data = {
            "trajectory": {
                "id": row[0],
                "name": row[1],
                "description": row[2],
                "detailed_description": row[3],
                "sample_interval_ms": row[4],
                "total_frames": row[5],
                "duration_sec": row[6],
                "created_at": row[7],
                "updated_at": row[8],
            },
            "frames": [
                {
                    "frame_index": f[0],
                    "timestamp_ms": f[1],
                    "left_arm": json.loads(f[2]),
                    "right_arm": json.loads(f[3]),
                    "left_hand": json.loads(f[4]),
                    "right_hand": json.loads(f[5]),
                }
                for f in frames
            ],
        }
        
        # 创建 output 目录（在 trajectory_manager 目录下）
        output_dir = Path(__file__).parent / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 保存为 JSON 文件
        traj_name = row[1]
        output_file = output_dir / f"{traj_name}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)
        
        return {
            "ok": True,
            "message": f"轨迹已导出到: {output_file}",
            "path": str(output_file),
        }
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    finally:
        await db.close()


@app.get("/api/status")
async def get_status():
    if not bridge:
        return {"error": "bridge not ready"}
    snapshot = bridge.get_snapshot()
    return {
        **snapshot.to_dict(),
        "mode": bridge.mode,
        "current_config": bridge.current_config,
    }


# ── WebSocket ────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.append(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"type": "error", "error": "invalid json"}))
                continue

            action = msg.get("action", "")
            result = await handle_ws_action(action, msg)
            await ws.send_text(json.dumps({"type": "result", "action": action, **result}))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if ws in ws_clients:
            ws_clients.remove(ws)


async def handle_ws_action(action: str, msg: dict) -> dict:
    global recorder, player

    if action == "set_mode":
        mode = msg.get("mode", "idle")
        if mode not in ("idle", "limp", "lock"):
            return {"ok": False, "error": "无效模式"}
        bridge.set_mode(mode)
        return {"ok": True, "mode": mode}

    elif action == "set_joint_mode":
        motor_ids = msg.get("motor_ids", [])
        mode = msg.get("mode", "limp")
        if mode not in ("idle", "limp", "lock"):
            return {"ok": False, "error": "无效模式"}
        bridge.set_joint_mode(motor_ids, mode)
        return {"ok": True, "joint_modes": bridge.joint_modes}

    elif action == "set_current":
        motor_ids = msg.get("motor_ids", ALL_ARM_IDS)
        current = float(msg.get("current", 1.5))
        bridge.set_current(motor_ids, current)
        return {"ok": True}

    elif action == "set_hand":
        side = msg.get("side", "both")
        angles = msg.get("angles", [1.0] * 6)
        bridge.set_hand(side, angles)
        return {"ok": True}

    elif action == "start_record":
        name = msg.get("name", "").strip()
        description = msg.get("description", "").strip()
        detailed_description = msg.get("detailed_description", "").strip()
        if not name:
            return {"ok": False, "error": "轨迹名称不能为空"}
        interval_ms = int(msg.get("interval_ms", 100))
        if bridge.mode == "idle":
            bridge.set_mode("limp")
        recorder.start(name, interval_ms, description, detailed_description)
        return {
            "ok": True,
            "name": name,
            "interval_ms": interval_ms,
            "description": description,
            "detailed_description": detailed_description,
        }

    elif action == "stop_record":
        tid = await recorder.stop()
        return {"ok": True, "trajectory_id": tid, "frames": len(recorder.frames)}

    elif action == "play":
        tid = int(msg.get("trajectory_id", 0))
        speed = float(msg.get("speed", 1.0))
        repeat = int(msg.get("repeat", 1))
        interval_sec = float(msg.get("interval_sec", 0.0))
        try:
            # 确保回放前机器人处于空闲状态
            if bridge and bridge.mode != "idle":
                bridge.set_mode("idle")
            await player.play(tid, speed, repeat, interval_sec)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif action == "stop_play":
        await player.stop()
        return {"ok": True}

    elif action == "pause_play":
        try:
            await player.pause()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif action == "resume_play":
        try:
            await player.resume()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif action == "body_delta":
        target = msg.get("target", "")  # "leg" or "waist"
        motor_id = int(msg.get("motor_id", 0))
        delta = float(msg.get("delta", 0.005))
        bridge.set_body(target, motor_id, delta)
        return {"ok": True, "body": bridge.get_body_status()}

    elif action == "body_pos":
        target = msg.get("target", "")
        motor_id = int(msg.get("motor_id", 0))
        pos = float(msg.get("pos", 0.0))
        bridge.set_body_pos(target, motor_id, pos)
        return {"ok": True, "body": bridge.get_body_status()}

    elif action == "body_preset":
        preset = msg.get("preset", "stand")
        bridge.set_body_preset(preset)
        return {"ok": True, "body": bridge.get_body_status()}

    elif action == "stop_play":
        await player.stop()
        return {"ok": True}

    return {"ok": False, "error": f"未知动作: {action}"}


# ── 静态文件 & 启动 ──────────────────────────────────────────────

URDF_DIR = Path(__file__).parent.parent.parent / "tianyi2_urdf"

# 挂载 URDF meshes 供前端 Three.js 加载
if URDF_DIR.exists():
    app.mount("/urdf/meshes", StaticFiles(directory=str(URDF_DIR / "meshes")), name="meshes")
    app.mount("/urdf/urdf", StaticFiles(directory=str(URDF_DIR / "urdf")), name="urdf_files")

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def main():
    print("=" * 50)
    print("  轨迹管理器 - Web 控制台")
    print("  http://localhost:1255")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=1255, log_level="info")


if __name__ == "__main__":
    main()
