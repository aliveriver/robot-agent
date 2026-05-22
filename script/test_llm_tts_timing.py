#!/usr/bin/env python3
"""test_llm_tts_timing.py - 最小化 LLM + TTS 耗时测试

只走 LLM 推理 + TTS 合成播放，不含 ASR、相机、memory、tools 等。
复用同一套 .env 与 configs/app.yaml。

用法:
  cd robot-agent
  python script/test_llm_tts_timing.py
  python script/test_llm_tts_timing.py --text "你叫什么名字？" --no-play
"""

from __future__ import annotations

import argparse
import asyncio
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
from src.robot_agent.capabilities.tts.minimax_adapter import get_tts
from src.robot_agent.settings import settings

logger = get_logger(__name__)

SYSTEM_PROMPT = (
    "你是一个友好的机器人助手，名叫天轶。请用简短的中文回答。"
)


async def run(text: str, play_audio: bool) -> None:
    timings: dict[str, float] = {}

    logger.info(
        "test: config loaded",
        llm_model=settings.llm.model,
        llm_url=settings.llm.api_url,
        tts_provider=settings.tts.provider,
        tts_ws_url=settings.tts.ws_url,
        tts_voice=settings.tts.default_voice,
        play_audio=play_audio,
    )

    # ── Step 1: LLM ──────────────────────────────────────────
    logger.info("test: [LLM] sending request", user_text=text)
    model = get_chat_model(multimodal=False)
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=text),
    ]

    t0 = time.perf_counter()
    response = await model.ainvoke(messages)
    t1 = time.perf_counter()

    llm_elapsed = t1 - t0
    timings["llm"] = llm_elapsed
    reply_text = response.content

    logger.info(
        "test: [LLM] done",
        elapsed_ms=round(llm_elapsed * 1000),
        reply_len=len(reply_text),
        reply=reply_text[:200],
    )

    # ── Step 2: TTS ──────────────────────────────────────────
    if not play_audio:
        logger.info("test: [TTS] skipped (--no-play)")
    else:
        logger.info("test: [TTS] starting synthesis + playback", text_len=len(reply_text))
        tts = get_tts()

        t2 = time.perf_counter()
        await tts.speak(reply_text, lang=settings.lang)
        t3 = time.perf_counter()

        tts_elapsed = t3 - t2
        timings["tts"] = tts_elapsed
        logger.info("test: [TTS] done", elapsed_ms=round(tts_elapsed * 1000))

    # ── Summary ──────────────────────────────────────────────
    total = sum(timings.values())
    timings["total"] = total

    logger.info(
        "test: === TIMING SUMMARY ===",
        llm_ms=round(timings.get("llm", 0) * 1000),
        tts_ms=round(timings.get("tts", 0) * 1000),
        total_ms=round(total * 1000),
    )
    # 额外用 print 输出一份易读版本
    print("\n" + "=" * 50)
    print("  TIMING SUMMARY")
    print("=" * 50)
    print(f"  LLM 推理:    {timings.get('llm', 0)*1000:.0f} ms")
    if "tts" in timings:
        print(f"  TTS 合成+播放: {timings['tts']*1000:.0f} ms")
    else:
        print("  TTS:         (skipped)")
    print(f"  总耗时:      {total*1000:.0f} ms")
    print("=" * 50)
    print(f"  LLM 回复: {reply_text[:120]}")
    print("=" * 50 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM + TTS 最小耗时测试")
    parser.add_argument(
        "--text",
        default="请用一句话介绍你自己。",
        help="模拟用户输入",
    )
    parser.add_argument(
        "--no-play",
        action="store_true",
        help="只测 LLM，不播放 TTS 音频",
    )
    args = parser.parse_args()

    setup_logging()
    asyncio.run(run(text=args.text, play_audio=not args.no_play))


if __name__ == "__main__":
    main()
