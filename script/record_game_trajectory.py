"""独立录制猜拳轨迹：复用 Recorder/RosBridge，保存到轨迹管理器的 SQLite。

运行 python -m script.record_game_trajectory；回车开始、回车停止并保存。
只录制到出手位置；判断完毕后，游戏会沿此轨迹倒序收回。
成功后更新 .env 的 GAME_TRAJECTORY_ID，供 play_game.py 直接回放。
支持 --name、--interval-ms、--simulate（显式模拟调试）和 --keep-selection。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def read_command(prompt: str) -> str:
    """后台读取终端，事件循环继续采样；退出不等待阻塞的输入线程。"""
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def read():
        try:
            value = input(prompt).strip().lower()
        except EOFError:
            value = "q"

        def deliver():
            if not future.done():
                future.set_result(value)

        if not loop.is_closed():
            loop.call_soon_threadsafe(deliver)

    threading.Thread(target=read, daemon=True).start()
    return await future


async def record_session(recorder, bridge, name: str, interval_ms: int, wait_stop) -> int:
    """松臂录制，停止后保持当前姿态并由共享 Recorder 完成持久化。"""
    bridge.set_mode("limp")
    recorder.start(name, interval_ms)
    try:
        await wait_stop()
    finally:
        bridge.set_mode("lock")
        # 包括正常结束、终端 EOF 和 Ctrl+C 取消；已有帧均交给共享保存逻辑。
        trajectory_id = await recorder.stop()
    if not trajectory_id:
        raise RuntimeError("没有采集到任何帧，本次没有保存轨迹")
    return trajectory_id


def select_for_game(trajectory_id: int, env_file: Path) -> None:
    """仅更新新轨迹的 ID，保留 .env 中其余 API、音频和设备配置。"""
    from dotenv import set_key

    set_key(str(env_file), "GAME_TRAJECTORY_ID", str(trajectory_id), quote_mode="never")


async def run(args) -> None:
    """初始化共享数据库和 ROS 桥接，交互录制一次并打印保存结果。"""
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from script.trajectory_manager.db import DB_PATH, get_db, init_db
    from script.trajectory_manager.recorder import Recorder
    from script.trajectory_manager.ros_bridge import RosBridge, SimBridge

    await init_db()
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM trajectories WHERE name = ?", (args.name,))
        if await cursor.fetchone() is not None:
            raise ValueError(f"轨迹名称已存在，请换一个 --name：{args.name}")
    finally:
        await db.close()

    # 实机模式失败就报错；只有显式 --simulate 时才能生成模拟帧。
    bridge = SimBridge() if args.simulate else RosBridge()
    try:
        if not args.simulate:
            await asyncio.to_thread(bridge.wait_ready)
        print(f"轨迹数据库：{DB_PATH.resolve()}")
        print(f"名称：{args.name}，采样周期：{args.interval_ms} ms")
        print("只录到出手位置，不录收回动作；游戏判定完输赢后会倒序收回。")
        if args.simulate:
            print("模拟模式：不会采集真实机器人动作，也不会自动选为猜拳轨迹。")
        if await read_command("回车开始录制；输入 q 退出：") == "q":
            return

        last_report = -1

        def report(count, elapsed):
            nonlocal last_report
            if int(elapsed) != last_report:
                last_report = int(elapsed)
                print(f"录制中：{count} 帧，{elapsed:.1f} 秒", flush=True)

        recorder = Recorder(bridge, on_frame=report)
        trajectory_id = await record_session(
            recorder, bridge, args.name, args.interval_ms,
            lambda: read_command("正在录制，可拖动双臂；到出手位置后回车停止并保存："),
        )
        print(f"已保存：ID={trajectory_id}，名称={args.name}，帧数={len(recorder.frames)}")
        if not args.keep_selection and not args.simulate:
            try:
                select_for_game(trajectory_id, ROOT / ".env")
                print(f"已选择为猜拳轨迹：GAME_TRAJECTORY_ID={trajectory_id}")
            except OSError as exc:
                print(f"轨迹已保存，但 .env 更新失败：{exc}")
        print(f"再次回放：GAME_TRAJECTORY_ID={trajectory_id} python -m script.play_game")
    finally:
        bridge.destroy()


def main() -> None:
    """解析最小录制参数，不启动 Agent、麦克风、TTS 或相机。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="game_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--interval-ms", type=int, default=10, help="采样周期 10–5000 ms，默认 10")
    parser.add_argument("--simulate", action="store_true", help="显式使用模拟状态调试")
    parser.add_argument("--keep-selection", action="store_true", help="仅保存，不更新猜拳轨迹 ID")
    args = parser.parse_args()
    args.name = args.name.strip()
    if not args.name or not 10 <= args.interval_ms <= 5000:
        parser.error("名称不能为空，采样周期必须在 10–5000 ms 之间")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("录制已中断。请回车正常停止，以获取已保存的轨迹 ID。")
    except Exception as exc:
        print(f"录制失败：{exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
