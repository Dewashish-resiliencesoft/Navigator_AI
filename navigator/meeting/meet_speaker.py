"""Speaker that pushes audio into a Meet/Zoom bot via Attendee."""

from __future__ import annotations

import io
import time
import wave
from collections.abc import Callable
from typing import Protocol

from navigator.meeting.attendee import AttendeeClient
from navigator.voice.tts import Speaker


class WavSynthesizer(Protocol):
    def synthesize_wav(self, text: str) -> bytes | None: ...


def wav_duration_s(wav: bytes) -> float:
    """Seconds of audio in a WAV blob. 0 if unreadable.

    Fish Audio (and some streaming encoders) write data-chunk size as
    0xFFFFFFFF — Python's wave module then reports ~hours of audio. Cap by
    actual payload bytes so MeetSpeaker does not wait forever after speak.
    """
    try:
        with wave.open(io.BytesIO(wav), "rb") as wf:
            rate = wf.getframerate()
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            if rate <= 0 or channels <= 0 or width <= 0:
                return 0.0
            frames = wf.getnframes()
            frame_bytes = channels * width
            # Locate data payload start (skip RIFF header + fmt).
            data_off = wav.find(b"data")
            if data_off >= 0 and data_off + 8 <= len(wav):
                payload = len(wav) - (data_off + 8)
                max_frames = payload // frame_bytes
                # Bogus header: claimed frames far beyond file.
                if max_frames > 0 and frames > max_frames + rate:  # >1s slack
                    frames = max_frames
            return frames / float(rate)
    except wave.Error:
        return 0.0


class MeetSpeaker:
    """Meet TTS only — no local speaker play (avoids double audio / overlap)."""

    def __init__(
        self,
        local: Speaker,
        attendee: AttendeeClient,
        bot_id: str,
        *,
        synthesizer: WavSynthesizer | None = None,
        also_chat: bool = False,
        after_speak: Callable[[], None] | None = None,
        set_avatar_state: Callable[[str], None] | None = None,
        playback_pad_s: float = 0.35,
        short_pad_s: float = 0.12,
        short_chars: int = 72,
        check_barge_in: Callable[[], bool] | None = None,
    ) -> None:
        self.local = local
        self.attendee = attendee
        self.bot_id = bot_id
        self.synthesizer = synthesizer
        self.also_chat = also_chat
        self.after_speak = after_speak
        self.set_avatar_state = set_avatar_state
        self.playback_pad_s = playback_pad_s
        self.short_pad_s = short_pad_s
        self.short_chars = short_chars
        self.check_barge_in = check_barge_in
        self.last_spoken = ""
        self.interrupted = False
        self.bot_ended = False

    def say(self, text: str) -> None:
        # Print only — do NOT local.say() (that plays Piper on host AND Meet).
        print(f"[speak] {text}", flush=True)
        self.last_spoken = text
        self.interrupted = False
        if self.also_chat and text.strip():
            try:
                self.attendee.send_chat(self.bot_id, text)
            except Exception as exc:  # noqa: BLE001
                print(f"[speak] Meet chat failed: {exc}", flush=True)
        synth = self.synthesizer
        if synth is None and hasattr(self.local, "synthesize_wav"):
            synth = self.local  # type: ignore[assignment]
        if synth is None or not text.strip():
            if text.strip() and synth is None:
                print(
                    "[speak] WARNING: no WAV synthesizer — Meet participants hear silence",
                    flush=True,
                )
            return
        try:
            wav = synth.synthesize_wav(text)
            if wav:
                if self.set_avatar_state is not None:
                    self.set_avatar_state("speaking")
                try:
                    self.attendee.speak(self.bot_id, wav)
                    print("[speak] Meet audio sent", flush=True)
                    pad = (
                        self.short_pad_s
                        if len(text.strip()) <= self.short_chars
                        else self.playback_pad_s
                    )
                    wait = wav_duration_s(wav) + pad
                    self._wait_playback(wait)
                    if self.after_speak is not None:
                        self.after_speak()
                finally:
                    if self.set_avatar_state is not None:
                        self.set_avatar_state("idle")
            else:
                print("[speak] WARNING: synthesize_wav returned empty", flush=True)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "state ended" in msg.lower() or "cannot play media" in msg.lower():
                self.bot_ended = True
                print("[speak] bot ended — stopping further Meet TTS", flush=True)
            print(f"[speak] Meet audio failed: {exc}", flush=True)

    def _wait_playback(self, wait_s: float) -> None:
        """Sleep for playback; poll barge-in so continuous user speech can cut in."""
        if wait_s <= 0:
            return
        deadline = time.monotonic() + wait_s
        # Ignore barge-in during the first slice — bot audio just started.
        ignore_until = time.monotonic() + min(0.45, wait_s * 0.35)
        while time.monotonic() < deadline:
            slice_s = min(0.15, deadline - time.monotonic())
            if slice_s <= 0:
                break
            time.sleep(slice_s)
            if (
                self.check_barge_in is not None
                and time.monotonic() >= ignore_until
                and self.check_barge_in()
            ):
                self.interrupted = True
                print("[speak] barge-in — cutting remaining wait", flush=True)
                return
