"""TTS speakers: Fish Audio (main) + Piper (local fallback).

Piper is GPL-3.0 (OHF-Voice/piper1-gpl). Fish uses cloud S2.1 Pro free + Sarah.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Protocol

from navigator.voice.fish_tts import FishSpeaker


class Speaker(Protocol):
    """What SPEAKING depends on. Swappable without touching the state machine."""

    def say(self, text: str) -> None: ...


def make_speaker(
    *,
    mute: bool = False,
    fish_api_key: str = "",
    fish_model: str = "s2.1-pro-free",
    fish_reference_id: str = "",
    tts_provider: str = "auto",
    piper_voice: str = "en_US-lessac-medium",
    piper_data_dir: str | Path = "voices",
    require_audio: bool = False,
) -> Speaker:
    """Pick Fish (main) or Piper. require_audio=True raises if Meet would be silent."""
    if mute:
        return PrintSpeaker()
    provider = (tts_provider or "auto").strip().lower()
    want_fish = provider == "fish" or (
        provider == "auto" and bool((fish_api_key or "").strip())
    )
    if want_fish:
        fish = FishSpeaker(
            fish_api_key,
            reference_id=fish_reference_id
            or "3a7a3d3df82948c6bd756761d6b139b5",
            model=fish_model or "s2.1-pro-free",
        )
        if fish.available():
            return fish
        if provider == "fish" and require_audio:
            raise RuntimeError(
                "NAVIGATOR_TTS_PROVIDER=fish but NAVIGATOR_FISH_API_KEY is empty. "
                "Get a key at https://fish.audio/app/"
            )
    piper = PiperSpeaker(piper_voice, piper_data_dir)
    if piper.available():
        return piper
    if require_audio:
        raise RuntimeError(
            "No TTS available for Meet. Set NAVIGATOR_FISH_API_KEY "
            "(preferred, free S2.1 + Sarah) or install Piper:\n"
            f"  .venv/bin/pip install 'piper-tts>=1.4'\n"
            f"  .venv/bin/python -m piper.download_voices {piper_voice} "
            f"--data-dir {piper_data_dir}"
        )
    return PrintSpeaker()


class PiperSpeaker:
    def __init__(
        self,
        voice: str,
        data_dir: str | Path = "voices",
        python: str | None = None,
    ) -> None:
        self.voice = voice
        self.data_dir = Path(data_dir)
        self.python = python or sys.executable
        self._player = _find_player()
        self._voice = None  # lazy PiperVoice

    def available(self) -> bool:
        """Whether the voice model is actually present on disk."""
        return (self.data_dir / f"{self.voice}.onnx").exists()

    def say(self, text: str) -> None:
        print(f"[speak] {text}")
        if not text.strip() or not self.available():
            return

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "out.wav"
            if not self.synthesize_to(wav, text):
                return
            self._play(wav)

    def synthesize_to(self, wav_path: Path, text: str) -> bool:
        """Write Piper WAV to wav_path. Returns False on failure."""
        raw = self.synthesize_wav(text)
        if not raw:
            return False
        wav_path.write_bytes(raw)
        return True

    def synthesize_wav(self, text: str) -> bytes | None:
        if not text.strip() or not self.available():
            return None
        warm = self._synthesize_warm(text)
        if warm is not None:
            return warm
        return self._synthesize_cli(text)

    def _ensure_voice(self):
        if self._voice is not None:
            return self._voice
        try:
            from piper import PiperVoice
        except ImportError:
            return None
        onnx = self.data_dir / f"{self.voice}.onnx"
        self._voice = PiperVoice.load(str(onnx))
        return self._voice

    def _synthesize_warm(self, text: str) -> bytes | None:
        """In-process Piper — model stays loaded (~30ms after first call)."""
        try:
            voice = self._ensure_voice()
            if voice is None:
                return None
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                voice.synthesize_wav(text, wf)
            return buf.getvalue()
        except Exception as exc:  # noqa: BLE001
            print(f"[speak] warm piper failed ({exc}); trying CLI", flush=True)
            self._voice = None
            return None

    def _synthesize_cli(self, text: str) -> bytes | None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "out.wav"
            synth = subprocess.run(
                [
                    self.python,
                    "-m",
                    "piper",
                    "-m",
                    self.voice,
                    "--data-dir",
                    str(self.data_dir),
                    "-f",
                    str(wav_path),
                    "--",
                    text,
                ],
                capture_output=True,
                text=True,
            )
            if synth.returncode != 0:
                print(
                    f"[speak] piper failed: {synth.stderr.strip().splitlines()[-1:]}"
                )
                return None
            return wav_path.read_bytes()

    def _play(self, wav: Path) -> None:
        if self._player is None:
            print(f"[speak] no audio player found; wav written to {wav}")
            return
        subprocess.run([*self._player, str(wav)], capture_output=True)


class PrintSpeaker:
    """Speaker for tests and headless runs. Records what was said."""

    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text: str) -> None:
        self.said.append(text)
        print(f"[speak] {text}")


def _find_player() -> list[str] | None:
    candidates = (
        ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet"],
        ["aplay", "-q"],
        ["paplay"],
    )
    for cmd in candidates:
        if shutil.which(cmd[0]):
            return cmd
    return None
