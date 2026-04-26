"""音频设备发现和解析辅助工具。"""

from __future__ import annotations

from typing import Any

from src.robot_agent.bootstrap.logging import get_logger

logger = get_logger(__name__)


def _get_sounddevice():
    try:
        import sounddevice as sd
    except ImportError as exc:  # noqa: BLE001
        raise RuntimeError("音频设备发现需要安装 sounddevice") from exc
    return sd


def _channel_key(kind: str) -> str:
    return "max_input_channels" if kind == "input" else "max_output_channels"


def device_label(device_index: int | str | None) -> str:
    """返回单个设备的可读标签。"""
    if device_index is None:
        return "None"

    try:
        sd = _get_sounddevice()
        info = sd.query_devices(device_index)
        hostapis = sd.query_hostapis()
    except Exception:  # noqa: BLE001
        return str(device_index)

    hostapi_index = info.get("hostapi")
    hostapi_name = "unknown"
    if isinstance(hostapi_index, int) and 0 <= hostapi_index < len(hostapis):
        hostapi_name = hostapis[hostapi_index].get("name", "unknown")

    return (
        f"{device_index}: {info.get('name', 'unknown')} "
        f"[hostapi={hostapi_name}, in={info.get('max_input_channels', 0)}, "
        f"out={info.get('max_output_channels', 0)}]"
    )


def list_audio_devices() -> str:
    """返回用于日志记录的格式化音频设备列表。"""
    try:
        sd = _get_sounddevice()
        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        return f"(音频设备不可用: {exc})"

    lines: list[str] = []
    for index, info in enumerate(devices):
        in_ch = info.get("max_input_channels", 0)
        out_ch = info.get("max_output_channels", 0)
        if in_ch <= 0 and out_ch <= 0:
            continue
        lines.append(
            f"[{index}] {info.get('name', 'unknown')} "
            f"(in={in_ch}, out={out_ch}, hostapi={info.get('hostapi')})"
        )
    return "\n".join(lines) if lines else "(未找到音频设备)"


def resolve_device(preferred: int | str | None, kind: str) -> int | None:
    """通过索引、精确名称或部分名称解析一个首选设备。"""
    try:
        sd = _get_sounddevice()
        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        logger.warning("audio_device: 解析过程中查询失败", error=str(exc))
        return None

    channel_key = _channel_key(kind)

    def _supports(index: int | str | None) -> bool:
        try:
            int_index = int(index)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        return 0 <= int_index < len(devices) and devices[int_index].get(channel_key, 0) > 0

    if preferred in (None, ""):
        return None

    if isinstance(preferred, int):
        return preferred if _supports(preferred) else None

    if isinstance(preferred, str):
        candidate = preferred.strip()
        if not candidate:
            return None
        if candidate.isdigit():
            index = int(candidate)
            return index if _supports(index) else None

        candidate_lower = candidate.lower()
        for index, device in enumerate(devices):
            if device.get(channel_key, 0) <= 0:
                continue
            name = str(device.get("name", ""))
            if candidate_lower == name.lower():
                return index

        for index, device in enumerate(devices):
            if device.get(channel_key, 0) <= 0:
                continue
            name = str(device.get("name", ""))
            if candidate_lower in name.lower():
                return index

    return None


def first_supported_device(kind: str, exclude: int | None = None) -> int | None:
    """返回支持所请求方向的第一个设备。"""
    try:
        sd = _get_sounddevice()
        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        logger.warning("audio_device: 寻找第一个支持的设备时查询失败", error=str(exc))
        return None

    channel_key = _channel_key(kind)
    for index, device in enumerate(devices):
        if exclude is not None and index == exclude:
            continue
        if device.get(channel_key, 0) > 0:
            return index
    return None


def preferred_output_device(exclude: int | None = None) -> int | None:
    """当有多个可用设备时，选择一个可能是扬声器的输出设备。"""
    try:
        sd = _get_sounddevice()
        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        logger.warning("audio_device: 选择首选输出时查询失败", error=str(exc))
        return None

    ranked_keywords = [
        ("pulse", 0),
        ("usb audio", 1),
        ("usb", 2),
        ("speaker", 3),
        ("headphone", 4),
        ("default", 5),
        ("sysdefault", 6),
        ("dmix", 7),
        ("front", 8),
        ("hdmi", 50),
        ("ape", 60),
    ]

    candidates: list[tuple[int, int]] = []
    for index, device in enumerate(devices):
        if exclude is not None and index == exclude:
            continue
        if device.get("max_output_channels", 0) <= 0:
            continue
        name = str(device.get("name", "")).lower()
        score = 20
        for keyword, keyword_score in ranked_keywords:
            if keyword in name:
                score = min(score, keyword_score)
        candidates.append((score, index))

    if not candidates:
        return None

    candidates.sort()
    return candidates[0][1]


def resolve_audio_devices(
    input_preference: int | str | None = None,
    output_preference: int | str | None = None,
    allow_shared_device: bool = True,
) -> tuple[int | None, int | None]:
    """解析具有跨机器回退机制的输入和输出设备。"""
    try:
        sd = _get_sounddevice()
        default_in, default_out = sd.default.device
    except Exception as exc:  # noqa: BLE001
        logger.warning("audio_device: 读取默认设备失败", error=str(exc))
        default_in, default_out = None, None

    input_device = default_in
    output_device = default_out

    if input_preference not in (None, ""):
        resolved_input = resolve_device(input_preference, "input")
        if resolved_input is not None:
            input_device = resolved_input

    if output_preference not in (None, ""):
        resolved_output = resolve_device(output_preference, "output")
        if resolved_output is not None:
            output_device = resolved_output

    if input_device is None:
        input_device = first_supported_device("input")
    if output_device is None:
        output_device = first_supported_device("output")

    if not allow_shared_device and input_device is not None and input_device == output_device:
        output_device = preferred_output_device(exclude=input_device) or first_supported_device(
            "output",
            exclude=input_device,
        )

    return input_device, output_device


def resolve_input_device(preferred: int | str | None = None) -> int | None:
    """解析一个首选输入设备。"""
    resolved_input, _ = resolve_audio_devices(input_preference=preferred)
    return resolved_input


def resolve_output_device(
    preferred: int | str | None = None,
    exclude_input: int | None = None,
    allow_shared_device: bool = True,
) -> int | None:
    """解析一个首选输出设备。"""
    if preferred not in (None, ""):
        resolved = resolve_device(preferred, "output")
        if resolved is not None:
            if allow_shared_device or exclude_input is None or resolved != exclude_input:
                return resolved

    if not allow_shared_device and exclude_input is not None:
        return preferred_output_device(exclude=exclude_input) or first_supported_device(
            "output",
            exclude=exclude_input,
        )

    _, resolved_output = resolve_audio_devices(output_preference=preferred)
    return resolved_output


def log_audio_device_selection(input_device: Any, output_device: Any) -> None:
    """记录解析后的音频设备和当前可见的库存。"""
    logger.info(
        "audio_device: resolved devices",
        input_device=input_device,
        output_device=output_device,
        input_label=device_label(input_device),
        output_label=device_label(output_device),
    )
    logger.debug("audio_device: available devices\n" + list_audio_devices())
