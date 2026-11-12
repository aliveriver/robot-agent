"""Admit speech work atomically so device cleanup cannot submit stale commands."""
from __future__ import annotations

import asyncio
from concurrent.futures import Future, InvalidStateError
from threading import Lock
from typing import Coroutine, Any
from collections.abc import Callable


class VoiceTasks:
    """Own speech futures across the microphone thread and Agent event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop, can_submit=lambda: True) -> None:
        self._loop = loop
        self._lock = Lock()
        self._accepting = True
        self._pending: set[Future] = set()
        self._can_submit = can_submit

    def submit(self, work: Coroutine[Any, Any, Any] | Callable[[], Coroutine]) -> Future | None:
        """Reject late ASR results and close their unscheduled coroutines."""
        with self._lock:
            if not self._accepting or self._pending or not self._can_submit():
                if not callable(work):
                    work.close()
                return None
            coroutine = work() if callable(work) else work
            future = Future()
            self._pending.add(future)
        self._loop.call_soon_threadsafe(self._start, coroutine, future)
        return future

    def _start(self, coroutine: Coroutine, future: Future) -> None:
        """Bind admission to the actual asyncio task, not its early-cancelled bridge."""
        if future.cancelled():
            coroutine.close()
            with self._lock:
                self._pending.discard(future)
            return
        task = self._loop.create_task(coroutine)
        def cancel_async(done: Future) -> None:
            if done.cancelled():
                self._loop.call_soon_threadsafe(task.cancel)
        future.add_done_callback(cancel_async)
        def finished(done: asyncio.Task) -> None:
            # Only this callback releases admission after all async finally blocks end.
            with self._lock:
                self._pending.discard(future)
            try:
                if done.cancelled():
                    future.cancel()
                else:
                    error = done.exception()
                    if not future.done():
                        if error is not None:
                            future.set_exception(error)
                        else:
                            future.set_result(done.result())
            except InvalidStateError:
                pass  # A concurrent device shutdown cancelled the public future.
        task.add_done_callback(finished)

    def cancel_current(self) -> int:
        """Interrupt current speech while continuing to accept subsequent user speech."""
        with self._lock:
            pending = list(self._pending)
        return sum(future.cancel() for future in pending)

    def close(self) -> None:
        """Stop admission before cancellation, including results still being recognized."""
        with self._lock:
            self._accepting = False
            pending = list(self._pending)
        for future in pending:
            future.cancel()

    async def wait_closed(self) -> None:
        """Finish real cleanup before a replacement voice session can accept speech."""
        while True:
            with self._lock:
                if not self._pending:
                    return
            await asyncio.sleep(0.01)


class WelcomeOnce:
    """Play one welcome per process with the same echo tracking as conversation TTS."""

    def __init__(self) -> None:
        self._attempted = False

    async def play(self, tts, text: str, lang: str, session) -> None:
        """Coordinate with the graph and register speaker output before audio starts."""
        if self._attempted:
            return
        self._attempted = True
        async with session.graph_lock:
            session.note_spoken_start(text)
            try:
                await asyncio.wait_for(tts.speak(text, lang=lang,
                    interrupt_event=session.tts_interrupt_event), timeout=15)
            finally:
                session.note_spoken_finish()
