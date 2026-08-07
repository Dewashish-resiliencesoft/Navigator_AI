"""TTS speakers: Gemini Live (main) + Fish + Piper (legacy fallbacks).

Gemini Live: native audio, English + Hindi, warm Indian female voice (Sulafat).
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
from navigator.voice.gemini_live import GeminiLiveSpeaker
from navigator.voice.language import SpokenLanguage


class Speaker(Protocol):
    """What SPEAKING depends on. Swappable without touching the state machine."""

    def say(self, text: str) -> None: ...


class CascadeSpeaker:
    """Try TTS backends in order until one returns audio.

    Gemini Live often dies mid-demo (keepalive timeout) and then returns empty
    forever — without a cascade, Meet goes silent while clicks keep running.
    """

    def __init__(self, speakers: list) -> None:
        self._speakers = [s for s in speakers if s is not None]
        self._active = 0

    def available(self) -> bool:
        return any(
            getattr(s, "available", lambda: True)() for s in self._speakers
        )

    def set_language(self, lang: SpokenLanguage) -> None:
        for s in self._speakers:
            fn = getattr(s, "set_language", None)
            if callable(fn):
                fn(lang)

    def synthesize_wav(self, text: str) -> bytes | None:
        if not text.strip() or not self._speakers:
            return None
        n = len(self._speakers)
        for offset in range(n):
            i = (self._active + offset) % n
            s = self._speakers[i]
            name = type(s).__name__
            try:
                wav = s.synthesize_wav(text)
            except Exception as exc:  # noqa: BLE001
                print(f"[speak] {name} failed: {exc}", flush=True)
                wav = None
            if wav:
                if i != self._active:
                    print(f"[speak] cascaded TTS → {name}", flush=True)
                self._active = i
                return wav
            if offset == 0 and n > 1:
                nxt = type(self._speakers[(i + 1) % n]).__name__
                print(f"[speak] {name} silent — trying {nxt}", flush=True)
        return None

    def say(self, text: str) -> None:
        print(f"[speak] {text}", flush=True)
        self.synthesize_wav(text)

    def close(self) -> None:
        for s in self._speakers:
            fn = getattr(s, "close", None)
            if callable(fn):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass


def make_speaker(
    *,
    mute: bool = False,
    gemini_api_key: str = "",
    gemini_live_model: str = "gemini-2.5-flash-native-audio-preview-12-2025",
    gemini_live_voice: str = "Sulafat",
    spoken_language: SpokenLanguage = "en",
    fish_api_key: str = "",
    fish_model: str = "s2.1-pro-free",
    fish_reference_id: str = "",
    tts_provider: str = "auto",
    piper_voice: str = "en_US-lessac-medium",
    piper_data_dir: str | Path = "voices",
    require_audio: bool = False,
) -> Speaker:
    """Pick Gemini Live (main), Fish, or Piper. require_audio=True raises if silent."""
    if mute:
        return PrintSpeaker()
    provider = (tts_provider or "auto").strip().lower()
    chain: list = []

    want_gemini = provider in ("gemini", "auto") and bool(
        (gemini_api_key or "").strip()
    )
    if want_gemini or provider == "gemini":
        gemini = GeminiLiveSpeaker(
            gemini_api_key,
            model=gemini_live_model,
            voice_name=gemini_live_voice,
            spoken_language=spoken_language,
        )
        if gemini.available():
            chain.append(gemini)
        elif provider == "gemini" and require_audio and not chain:
            raise RuntimeError(
                "NAVIGATOR_TTS_PROVIDER=gemini but NAVIGATOR_GEMINI_API_KEY is empty."
            )

    want_fish = provider in ("fish", "auto") and bool((fish_api_key or "").strip())
    # Emergency fallback even when provider=gemini — silent Meet is worse.
    if want_fish or (provider == "gemini" and (fish_api_key or "").strip()):
        fish = FishSpeaker(
            fish_api_key,
            reference_id=fish_reference_id
            or "3a7a3d3df82948c6bd756761d6b139b5",
            model=fish_model or "s2.1-pro-free",
        )
        if fish.available():
            chain.append(fish)
        elif provider == "fish" and require_audio and not chain:
            raise RuntimeError(
                "NAVIGATOR_TTS_PROVIDER=fish but NAVIGATOR_FISH_API_KEY is empty. "
                "Get a key at https://fish.audio/app/"
            )

    piper = PiperSpeaker(piper_voice, piper_data_dir)
    if piper.available():
        chain.append(piper)

    if not chain:
        if require_audio:
            raise RuntimeError(
                "No TTS available for Meet. Set NAVIGATOR_GEMINI_API_KEY "
                "(Gemini Live, preferred) or NAVIGATOR_FISH_API_KEY or install Piper:\n"
                f"  .venv/bin/pip install 'piper-tts>=1.4'\n"
                f"  .venv/bin/python -m piper.download_voices {piper_voice} "
                f"--data-dir {piper_data_dir}"
            )
        return PrintSpeaker()
    if len(chain) == 1:
        return chain[0]
    return CascadeSpeaker(chain)

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
