"""Fish Audio TTS (S2.1 Pro free + Sarah) — main Meet voice."""

from __future__ import annotations

import io
import json
import wave
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Warm young conversational Sarah — https://fish.audio/m/3a7a3d3df82948c6bd756761d6b139b5/
DEFAULT_SARAH_ID = "3a7a3d3df82948c6bd756761d6b139b5"
API_URL = "https://api.fish.audio/v1/tts"
FREE_MODEL = "s2.1-pro-free"


def _is_mp3(data: bytes) -> bool:
    if data[:3] == b"ID3":
        return True
    return len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


class FishSpeaker:
    """Cloud TTS via Fish Audio. synthesize_mp3 for Meet (no ffmpeg)."""

    def __init__(
        self,
        api_key: str,
        *,
        reference_id: str = DEFAULT_SARAH_ID,
        model: str = FREE_MODEL,
        latency: str = "balanced",
        post=None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.reference_id = reference_id
        self.model = model
        self.latency = latency
        self._post = post or _http_post
        self._player = None

    def available(self) -> bool:
        return bool(self.api_key)

    def say(self, text: str) -> None:
        print(f"[speak] {text}", flush=True)
        _ = self.synthesize_mp3(text)

    def synthesize_mp3(self, text: str) -> bytes | None:
        """MP3 for Attendee output_audio — no local ffmpeg needed."""
        return self._synthesize(text, fmt="mp3")

    def synthesize_wav(self, text: str) -> bytes | None:
        return self._synthesize(text, fmt="wav")

    def _synthesize(self, text: str, *, fmt: str) -> bytes | None:
        if not text.strip() or not self.available():
            return None
        body: dict = {
            "text": text.strip(),
            "reference_id": self.reference_id,
            "format": fmt,
            "latency": self.latency,
        }
        if fmt == "mp3":
            body["mp3_bitrate"] = 128
        try:
            raw = self._post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "model": self.model,
                },
                body=body,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[speak] fish tts failed: {exc}", flush=True)
            return None
        if not raw or len(raw) < 16:
            print("[speak] fish tts returned empty", flush=True)
            return None
        if fmt == "mp3":
            if not _is_mp3(raw):
                print("[speak] fish tts: expected MP3, got other format", flush=True)
                return None
            return raw
        if raw[:4] != b"RIFF":
            print("[speak] fish tts: expected WAV (RIFF), got other format", flush=True)
            return None
        try:
            with wave.open(io.BytesIO(raw), "rb") as wf:
                if wf.getnframes() <= 0:
                    return None
        except wave.Error as exc:
            print(f"[speak] fish tts bad wav: {exc}", flush=True)
            return None
        return raw


def _http_post(url: str, *, headers: dict, body: dict) -> bytes:
    data = json.dumps(body).encode()
    req = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=60) as resp:
            return resp.read()
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
