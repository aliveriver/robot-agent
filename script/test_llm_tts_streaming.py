#!/usr/bin/env python3
"""test_llm_tts_streaming.py - LLM 流式 + TTS 流水线并行耗时测试

LLM 边生成 token 边按句切分，每凑够一句立刻送 TTS 合成播放，
不必等 LLM 全部输出完毕。TTS 使用单 WebSocket 连接，逐句发送，
避免重复握手带来的延迟和断续感。

用法:
  cd robot-agent
  python script/test_llm_tts_streaming.py
  python script/test_llm_tts_streaming.py --text "给我讲个笑话" --no-play
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import ssl
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

load_dotenv(ROOT_DIR / ".env")

from langchain_core.messages import HumanMessage, SystemMessage

from src.robot_agent.bootstrap.logging import get_logger, setup_logging
from src.robot_agent.capabilities.llm.chat_model import get_chat_model
from src.robot_agent.capabilities.tts.minimax_adapter import _StreamAudioPlayer
from src.robot_agent.settings import settings

logger = get_logger(__name__)

SYSTEM_PROMPT = "你是一个友好的机器人助手，名叫天轶。请用简短的中文回答。"

# 只在句号级别切分，保证语音流畅不断续
SENTENCE_DELIMITERS = re.compile(r"[。！？\n!?]")


async def tts_worker(
    sentence_queue: asyncio.Queue[str | None],
    play_audio: bool,
    timings: dict,
) -> None:
    """单 WebSocket 连接，逐句发送 TTS 请求，流式播放音频。"""
    if not play_audio:
        while True:
            sentence = await sentence_queue.get()
            if sentence is None:
                break
        return

    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError("缺少依赖 websockets") from exc

    tts_cfg = settings.tts
    api_key = tts_cfg.api_key.strip()
    auth = f"Bearer {api_key}" if not api_key.lower().startswith("bearer ") else api_key
    voice_id = tts_cfg.default_voice.strip() or "female-shaonv"

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    player = _StreamAudioPlayer(output_device=settings.audio.output_device)
    if not player.start():
        logger.error("tts: 无法初始化播放器")
        return

    websocket = None
    first_audio_logged = False
    sentence_idx = 0

    try:
        websocket = await websockets.connect(
            tts_cfg.ws_url,
            additional_headers={"Authorization": auth},
            ssl=ssl_context,
        )

        connected = json.loads(await websocket.recv())
        if connected.get("event") != "connected_success":
            raise RuntimeError(f"TTS 握手失败: {connected}")

        while True:
            sentence = await sentence_queue.get()
            if sentence is None:
                break

            sentence = sentence.strip()
            if not sentence:
                continue

            sentence_idx += 1
            t_sent_start = time.perf_counter()
            logger.info(f"tts: sentence #{sentence_idx}", text=sentence[:80])

            start_msg = {
                "event": "task_start",
                "model": tts_cfg.model,
                "voice_setting": {
                    "voice_id": voice_id,
                    "speed": tts_cfg.speed,
                    "vol": 1,
                    "pitch": 0,
                    "emotion": "neutral",
                },
                "audio_setting": {
                    "sample_rate": 32000,
                    "bitrate": 128000,
                    "format": "pcm",
                    "channel": 1,
                },
            }
            await websocket.send(json.dumps(start_msg))

            started = json.loads(await websocket.recv())
            if started.get("event") != "task_started":
                raise RuntimeError(f"TTS 任务启动失败: {started}")

            await websocket.send(json.dumps({"event": "task_continue", "text": sentence}))

            while True:
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=15.0)
                except asyncio.TimeoutError:
                    logger.warning("tts: recv timeout")
                    break

                resp = json.loads(raw)
                if resp.get("event") == "task_failed":
                    logger.error("tts: task_failed", detail=resp)
                    break

                audio_hex = resp.get("data", {}).get("audio")
                if audio_hex:
                    if not first_audio_logged:
                        timings["first_audio"] = time.perf_counter()
                        first_audio_logged = True
                        logger.info(
                            "tts: FIRST audio chunk",
                            latency_from_start_ms=round(
                                (timings["first_audio"] - timings["llm_start"]) * 1000
                            ),
                        )
                    player.write(audio_hex)

                if resp.get("is_final"):
                    break

            await websocket.send(json.dumps({"event": "task_finish"}))

            logger.info(
                f"tts: sentence #{sentence_idx} done",
                elapsed_ms=round((time.perf_counter() - t_sent_start) * 1000),
            )

    except Exception as exc:
        logger.error("tts: worker error", error=str(exc))
    finally:
        if websocket:
            try:
                await websocket.close()
            except Exception:
                pass
        player.finish()

    timings["sentences_total"] = sentence_idx


async def run(text: str, play_audio: bool) -> None:
    timings: dict[str, float] = {}

    logger.info(
        "streaming test: config",
        llm_model=settings.llm.model,
        llm_url=settings.llm.api_url,
        tts_provider=settings.tts.provider,
        play_audio=play_audio,
    )

    sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
    tts_task = asyncio.create_task(tts_worker(sentence_queue, play_audio, timings))

    model = get_chat_model(multimodal=False)
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=text),
    ]

    logger.info("llm: streaming start", user_text=text)
    timings["llm_start"] = time.perf_counter()

    full_reply = ""
    buffer = ""
    first_token_time = None
    token_count = 0
    sentences_sent = 0

    async for chunk in model.astream(messages):
        token_text = chunk.content
        if not token_text:
            continue

        token_count += 1
        if first_token_time is None:
            first_token_time = time.perf_counter()
            timings["first_token"] = first_token_time
            logger.info(
                "llm: first token",
                latency_ms=round((first_token_time - timings["llm_start"]) * 1000),
            )

        full_reply += token_text
        buffer += token_text

        parts = SENTENCE_DELIMITERS.split(buffer)
        if len(parts) > 1:
            for seg in parts[:-1]:
                seg = seg.strip()
                if seg:
                    sentences_sent += 1
                    await sentence_queue.put(seg)
            buffer = parts[-1]

    timings["llm_end"] = time.perf_counter()
    llm_elapsed = timings["llm_end"] - timings["llm_start"]

    if buffer.strip():
        sentences_sent += 1
        await sentence_queue.put(buffer.strip())

    await sentence_queue.put(None)

    logger.info(
        "llm: done",
        elapsed_ms=round(llm_elapsed * 1000),
        tokens=token_count,
        sentences=sentences_sent,
    )

    await tts_task
    timings["end"] = time.perf_counter()

    total = timings["end"] - timings["llm_start"]
    first_token_ms = round(
        (timings.get("first_token", timings["llm_start"]) - timings["llm_start"]) * 1000
    )
    first_audio_ms = (
        round((timings["first_audio"] - timings["llm_start"]) * 1000)
        if "first_audio" in timings
        else None
    )

    print("\n" + "=" * 58)
    print("  STREAMING TIMING SUMMARY")
    print("=" * 58)
    print(f"  LLM 首 token:            {first_token_ms} ms")
    if first_audio_ms is not None:
        print(f"  首次语音播放:            {first_audio_ms} ms  <-- 用户感知延迟")
    print(f"  LLM 全部完成:            {round(llm_elapsed * 1000)} ms  ({token_count} tokens)")
    print(f"  总耗时(含TTS播完):       {round(total * 1000)} ms")
    print(f"  生成句数:                {sentences_sent}")
    print("=" * 58)
    print(f"  LLM 回复: \n")
    print(f"  {full_reply}")
    print("=" * 58 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM 流式 + TTS 流水线并行耗时测试")
    parser.add_argument("--text", default="请用一句话介绍你自己。", help="模拟用户输入")
    parser.add_argument("--no-play", action="store_true", help="只测 LLM 流式，不播放 TTS")
    args = parser.parse_args()

    setup_logging()
    asyncio.run(run(text=args.text, play_audio=not args.no_play))


if __name__ == "__main__":
    main()
