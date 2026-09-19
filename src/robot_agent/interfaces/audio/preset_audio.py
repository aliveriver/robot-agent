"""Persistent generated-TTS WAV library backed by SQLite."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PresetAudioStore:
    """Generate, persist, and list named TTS WAV files."""

    def __init__(self, audio_dir: str | Path | None = None, db_path: str | Path | None = None) -> None:
        self.audio_dir = Path(audio_dir or Path.cwd() / "data" / "tts_audio")
        self.db_path = Path(db_path or Path.cwd() / "data" / "tts_audio.sqlite3")
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS preset_audio (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, text TEXT NOT NULL,
                filename TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
                duration_ms INTEGER NOT NULL)""")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    async def generate(self, name: str, text: str) -> dict[str, Any]:
        clean_name = name.strip()
        clean_text = text.strip()
        if not clean_name or len(clean_name) > 80:
            raise ValueError("名称不能为空且不能超过 80 个字符")
        if not clean_text:
            raise ValueError("文字内容不能为空")
        if len(clean_text) > 1000:
            raise ValueError("文字内容不能超过 1000 个字符")

        from src.robot_agent.capabilities.tts.minimax_adapter import get_tts
        audio_id = str(uuid.uuid4())
        filename = f"{audio_id}.wav"
        path = self.audio_dir / filename
        try:
            duration_ms = await get_tts().synthesize_to_wav(clean_text, path)
            created_at = datetime.now(timezone.utc).isoformat()
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO preset_audio VALUES (?, ?, ?, ?, ?, ?)",
                    (audio_id, clean_name, clean_text, filename, created_at, duration_ms),
                )
                conn.commit()
            return {"id": audio_id, "name": clean_name, "text": clean_text,
                    "created_at": created_at, "duration_ms": duration_ms}
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,name,text,created_at,duration_ms FROM preset_audio ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, audio_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM preset_audio WHERE id = ?", (audio_id,)).fetchone()
        if row is None:
            raise KeyError(audio_id)
        result = dict(row)
        result["path"] = self.audio_dir / result.pop("filename")
        if not result["path"].is_file():
            raise KeyError(audio_id)
        return result

    def delete(self, audio_id: str) -> dict[str, Any]:
        """Delete one preset and its WAV using its database ID."""
        audio = self.get(audio_id)
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM preset_audio WHERE id = ?", (audio_id,))
            if cursor.rowcount == 0:
                raise KeyError(audio_id)
            conn.commit()
        audio["path"].unlink(missing_ok=True)
        return {"id": audio_id, "deleted": True}


_store: PresetAudioStore | None = None


def get_preset_audio_store() -> PresetAudioStore:
    global _store
    if _store is None:
        _store = PresetAudioStore()
    return _store
