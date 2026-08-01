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


class FishSpeaker:
    """Cloud TTS via Fish Audio. synthesize_wav → WAV for Attendee."""

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
        wav = self.synthesize_wav(text)
        if not wav:
            return
        # Local play only if something wired later; Meet uses synthesize_wav.
        _ = wav

    def synthesize_wav(self, text: str) -> bytes | None:
        if not text.strip() or not self.available():
            return None
        try:
            raw = self._post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "model": self.model,
                },
                body={
                    "text": text.strip(),
                    "reference_id": self.reference_id,
                    "format": "wav",
                    "latency": self.latency,
                },
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[speak] fish tts failed: {exc}", flush=True)
            return None
        if not raw or len(raw) < 44:
            print("[speak] fish tts returned empty", flush=True)
            return None
        # Sanity: must look like WAV (RIFF) for Meet duration + Attendee path.
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
