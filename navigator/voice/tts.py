"""Local TTS via Piper.

Piper is GPL-3.0 (the MIT rhasspy/piper is archived; maintained work is at
OHF-Voice/piper1-gpl), so it is invoked as a subprocess rather than imported --
which also means a missing voice model degrades to printing instead of crashing a
live call.

ponytail: the CLI reloads the ONNX model on every call, ~1s of startup per
utterance. Fine for a scripted Phase 1 demo. When narration latency starts
mattering, switch to `python -m piper.http_server` and POST to it; the Speaker
interface below does not change.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Protocol


class Speaker(Protocol):
    """What SPEAKING depends on. Swappable without touching the state machine."""

    def say(self, text: str) -> None: ...


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

    def available(self) -> bool:
        """Whether the voice model is actually present on disk."""
        return (self.data_dir / f"{self.voice}.onnx").exists()

    def say(self, text: str) -> None:
        print(f"[speak] {text}")
        if not text.strip() or not self.available():
            return

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "out.wav"
            synth = subprocess.run(
                [
                    self.python, "-m", "piper",
                    "-m", self.voice,
                    "--data-dir", str(self.data_dir),
                    "-f", str(wav),
                    "--", text,
                ],
                capture_output=True,
                text=True,
            )
            if synth.returncode != 0:
                print(f"[speak] piper failed: {synth.stderr.strip().splitlines()[-1:]}")
                return
            self._play(wav)

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
