"""Single-session WAV playback with pause, resume, stop, and progress."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from src.robot_agent.interfaces.audio.device_resolver import resolve_output_device
from src.robot_agent.settings import settings


class AudioPlaybackController:
    """Play one local WAV at a time while retaining an exact frame position."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = "idle"
        self._session_id: str | None = None
        self._source_type: str | None = None
        self._source_id: str | None = None
        self._path: Path | None = None
        self._delete_on_finish = False
        self._current_frame = 0
        self._total_frames = 0
        self._sample_rate = 0

    def play(self, path: str | Path, source_type: str, source_id: str | None = None,
             delete_on_finish: bool = False) -> dict[str, Any]:
        """Stop the prior session and start a WAV from frame zero."""
        import soundfile as sf

        audio_path = Path(path)
        if not audio_path.is_file():
            raise FileNotFoundError(str(audio_path))
        with sf.SoundFile(audio_path) as audio:
            total_frames = len(audio)
            sample_rate = audio.samplerate

        self.stop()
        with self._condition:
            self._stop_event = threading.Event()
            self._state = "playing"
            self._session_id = str(uuid.uuid4())
            self._source_type = source_type
            self._source_id = source_id
            self._path = audio_path
            self._delete_on_finish = delete_on_finish
            self._current_frame = 0
            self._total_frames = total_frames
            self._sample_rate = sample_rate
            session_id = self._session_id
            self._thread = threading.Thread(target=self._run, args=(session_id,), daemon=True)
            self._thread.start()
            return self.status()

    def pause(self) -> dict[str, Any]:
        with self._condition:
            if self._state == "playing":
                self._state = "paused"
            return self.status()

    def resume(self) -> dict[str, Any]:
        with self._condition:
            if self._state == "paused":
                self._state = "playing"
                self._condition.notify_all()
            return self.status()

    def stop(self) -> dict[str, Any]:
        with self._condition:
            old_path = self._path
            should_delete = self._delete_on_finish
            self._stop_event.set()
            self._condition.notify_all()
            self._clear_locked()
        if should_delete and old_path is not None:
            old_path.unlink(missing_ok=True)
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            position_ms = int(self._current_frame * 1000 / self._sample_rate) if self._sample_rate else 0
            duration_ms = int(self._total_frames * 1000 / self._sample_rate) if self._sample_rate else 0
            return {
                "state": self._state,
                "session_id": self._session_id,
                "source_type": self._source_type,
                "source_id": self._source_id,
                "position_ms": position_ms,
                "duration_ms": duration_ms,
            }

    def _run(self, session_id: str) -> None:
        import sounddevice as sd
        import soundfile as sf

        with self._lock:
            path = self._path
        try:
            if path is None:
                return
            with sf.SoundFile(path) as audio, sd.OutputStream(
                samplerate=audio.samplerate,
                channels=audio.channels,
                dtype="float32",
                device=resolve_output_device(settings.audio.output_device),
            ) as stream:
                while not self._stop_event.is_set():
                    with self._condition:
                        while self._state == "paused" and not self._stop_event.is_set():
                            self._condition.wait(timeout=0.5)
                        if self._stop_event.is_set() or self._session_id != session_id:
                            return
                        frame = self._current_frame
                    audio.seek(frame)
                    chunk = audio.read(1024, dtype="float32", always_2d=True)
                    if len(chunk) == 0:
                        break
                    stream.write(chunk)
                    with self._lock:
                        if self._session_id == session_id:
                            self._current_frame += len(chunk)
        finally:
            self._finish(session_id, path)

    def _finish(self, session_id: str, path: Path | None) -> None:
        with self._condition:
            if self._session_id != session_id:
                return
            should_delete = self._delete_on_finish
            self._clear_locked()
        if should_delete and path is not None:
            path.unlink(missing_ok=True)

    def _clear_locked(self) -> None:
        self._state = "idle"
        self._session_id = None
        self._source_type = None
        self._source_id = None
        self._path = None
        self._delete_on_finish = False
        self._current_frame = 0
        self._total_frames = 0
        self._sample_rate = 0
        self._thread = None


_controller = AudioPlaybackController()


def get_playback_controller() -> AudioPlaybackController:
    """Return the process-wide local WAV playback controller."""
    return _controller
