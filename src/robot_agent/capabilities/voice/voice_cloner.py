"""
voice_cloner.py - 声音克隆能力

负责录制一段用户语音样本，上传到声音克隆服务，并可播放服务端返回的流式试听音频。

主要接口:
    - `record_audio(...)`
    - `upload_voice(...)`
    - `play_stream(...)`

用法:
    from src.robot_agent.capabilities.voice.voice_cloner import VoiceCloner

    cloner = VoiceCloner(device="0")
    cloner.record_audio("sample.wav", duration=10)
    cloner.upload_voice("sample.wav", language="cn")
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd
import soundfile as sf


class VoiceCloner:
    """声音克隆服务客户端。"""

    def __init__(
        self,
        base_url: str = "https://kno-abkzopl0tfqssuv63-3d4qge3e-custom.service.onethingrobot.com",
        device: str | int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.device = device
        self.contract_text_cn = (
            "数据流穿过我的声带上传到数字空间。"
            "我在此刻声明，数字永生已经实现。"
        )
        self.contract_text_en = (
            "The data stream uploads to the digital void through my vocal cords. "
            "I declare, at this moment, digital immortality is achieved."
        )

    def record_audio(
        self,
        filename: str = "contract.wav",
        duration: int = 10,
        sample_rate: int = 16000,
    ) -> bool:
        """录制一段单声道语音样本并保存为 WAV。"""
        target_path = Path(filename)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        use_sample_rate = sample_rate
        try:
            sd.check_input_settings(
                device=self.device,
                channels=1,
                dtype="int16",
                samplerate=sample_rate,
            )
        except Exception:
            try:
                device_info = sd.query_devices(self.device, "input")
                use_sample_rate = int(device_info["default_samplerate"])
            except Exception:
                use_sample_rate = sample_rate

        try:
            audio_data = sd.rec(
                int(duration * use_sample_rate),
                samplerate=use_sample_rate,
                channels=1,
                dtype="int16",
                device=self.device,
            )
            sd.wait()

            if use_sample_rate != sample_rate:
                audio_data = self._resample_audio(
                    audio_data=audio_data,
                    orig_sample_rate=use_sample_rate,
                    target_sample_rate=sample_rate,
                )

            sf.write(str(target_path), audio_data, sample_rate)
            return True
        except Exception:
            return False

    def _resample_audio(
        self,
        audio_data: np.ndarray,
        orig_sample_rate: int,
        target_sample_rate: int,
    ) -> np.ndarray:
        """在采样率不匹配时，用 torchaudio 做重采样。"""
        import torch
        import torchaudio

        audio_float = audio_data.astype(np.float32) / 32768.0
        samples_tensor = torch.from_numpy(audio_float).transpose(0, 1)
        resampler = torchaudio.transforms.Resample(
            orig_freq=orig_sample_rate,
            new_freq=target_sample_rate,
        )
        resampled_tensor = resampler(samples_tensor).transpose(0, 1).numpy()
        return np.clip(resampled_tensor * 32768.0, -32768.0, 32767.0).astype(np.int16)

    def upload_voice(self, wav_path: str, language: str = "cn") -> bool:
        """上传语音样本并完成声音克隆签约。"""
        url = f"{self.base_url}/sign_contract"
        prompt_text = self.contract_text_cn if language == "cn" else self.contract_text_en

        try:
            with open(wav_path, "rb") as handle:
                files = {"file": (Path(wav_path).name, handle, "audio/wav")}
                data = {"prompt_text": prompt_text}
                response = requests.post(url, files=files, data=data, timeout=60)
            return response.status_code == 200
        except Exception:
            return False

    def play_stream(self, text: str) -> None:
        """播放服务端返回的流式试听音频。"""
        url = f"{self.base_url}/tts_stream"

        player_cmd = self._build_player_command()
        if not player_cmd:
            return

        try:
            response = requests.get(url, params={"text": text}, stream=True, timeout=60)
            if response.status_code != 200:
                return

            process = subprocess.Popen(
                player_cmd,
                stdin=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )

            try:
                for chunk in response.iter_content(chunk_size=4096):
                    if not chunk:
                        continue
                    if process.poll() is not None:
                        break
                    if process.stdin is None:
                        break
                    process.stdin.write(chunk)
                    process.stdin.flush()
            except BrokenPipeError:
                pass
            finally:
                if process.stdin is not None:
                    try:
                        process.stdin.close()
                    except BrokenPipeError:
                        pass
                process.wait(timeout=5)
        except Exception:
            return

    def _build_player_command(self) -> list[str]:
        """
        构造本地播放器命令。

        优先使用 Linux 机器人环境中的 `aplay`，若不存在则尝试 `ffplay`。
        """
        if shutil.which("aplay"):
            return [
                "aplay",
                "-f",
                "S16_LE",
                "-r",
                "26000",
                "-c",
                "1",
                "-t",
                "raw",
                "-D",
                "plughw:1,0",
            ]

        if shutil.which("ffplay"):
            return [
                "ffplay",
                "-autoexit",
                "-nodisp",
                "-f",
                "s16le",
                "-ar",
                "26000",
                "-ac",
                "1",
                "-",
            ]

        return []
