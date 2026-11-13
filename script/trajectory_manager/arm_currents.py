"""双臂电流配置：从同目录 JSON 加载，供猜拳、原轨迹播放器和录制桥接复用。"""

import json
import math
from pathlib import Path


def load_arm_currents() -> dict[int, float]:
    """读取全部 14 个关节的电流上限（A），配置缺失或数值无效时停止加载。"""
    config_path = Path(__file__).with_suffix(".json")
    values = json.loads(config_path.read_text(encoding="utf-8"))
    currents = {int(mid): float(value) for mid, value in values.items()}
    if set(currents) != set(range(11, 18)) | set(range(21, 28)):
        raise ValueError(f"双臂电流配置必须包含完整 14 个关节：{config_path}")
    if any(not math.isfinite(value) or value <= 0 for value in currents.values()):
        raise ValueError(f"双臂电流必须为有限正数：{config_path}")
    return currents


ARM_CURRENTS = load_arm_currents()
