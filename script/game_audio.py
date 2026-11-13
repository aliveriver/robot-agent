"""Resolve named game WAV presets from SQLite without loading Agent settings.

resolve_game_audio(root, cue) reads metadata only; playback remains in game_round.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


_CUE_KEYS = {
    'countdown': 'GAME_COUNTDOWN_PRESET',
    'win': 'GAME_PLAYER_WIN_PRESET',
    'lose': 'GAME_ROBOT_WIN_PRESET',
}


def resolve_game_audio(root: Path, cue: str) -> Path:
    """Select the newest exact-name preset and require its WAV file to exist."""
    key = _CUE_KEYS[cue]
    name = os.getenv(key, '').strip()
    if not name:
        raise ValueError(f'请配置 {key} 对应的预设音频名称')
    database = Path(os.getenv('GAME_AUDIO_DB', 'data/tts_audio.sqlite3')).expanduser()
    directory = Path(os.getenv('GAME_AUDIO_DIR', 'data/tts_audio')).expanduser()
    if not database.is_absolute():
        database = root / database
    if not directory.is_absolute():
        directory = root / directory
    # Read-only mode prevents a wrong path from creating an empty database.
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        row = conn.execute(
            'SELECT filename FROM preset_audio WHERE name = ? ORDER BY created_at DESC, rowid DESC LIMIT 1',
            (name,),
        ).fetchone()
    if row is None:
        raise LookupError(f'音频数据库中没有预设: {name}')
    audio = directory / row[0]
    if not audio.is_file():
        raise FileNotFoundError(f'预设 {name} 对应的音频文件不存在: {audio}')
    return audio
