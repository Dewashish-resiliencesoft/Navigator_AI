"""Attendee API client -- meeting bot for Zoom / Google Meet."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BotState = Literal["joining", "joined", "leaving", "ended", "fatal_error"]

# Attendee returns fine-grained states; we collapse to what JOINING cares about.
_STATE_MAP: dict[str, BotState] = {
    "ready": "joining",
    "joining": "joining",
    "waiting_room": "joining",
    "joined_not_recording": "joined",
    "joined_recording": "joined",
    "joined": "joined",
    "leaving": "leaving",
    "post_processing": "ended",
    "ended": "ended",
    "fatal_error": "fatal_error",
}


@dataclass
class Bot:
    id: str
    state: BotState
    raw_state: str = ""


class AttendeeClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        data = None if body is None else json.dumps(body).encode()
        req = Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Token {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=60) as resp:
                raw = resp.read() or b"{}"
        except HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise RuntimeError(f"Attendee {method} {path} -> {e.code}: {detail}") from e
        except URLError as e:
            raise RuntimeError(f"Attendee unreachable at {self.base_url}: {e}") from e
        return json.loads(raw) if raw else {}

    def join(
        self,
        meeting_url: str,
        bot_name: str = "Navigator AI",
        voice_agent_url: str | None = None,
    ) -> Bot:
        payload: dict = {"meeting_url": meeting_url, "bot_name": bot_name}
        if voice_agent_url:
            payload["voice_agent_settings"] = {"url": voice_agent_url}
        return self._bot(self._request("POST", "/bots", payload))

    def get(self, bot_id: str) -> Bot:
        return self._bot(self._request("GET", f"/bots/{bot_id}"))

    def leave(self, bot_id: str) -> None:
        self._request("POST", f"/bots/{bot_id}/leave", {})

    def speak(self, bot_id: str, wav: bytes) -> None:
        raise NotImplementedError("speak lands with Piper→Meet wiring")

    def audio_stream(self, bot_id: str):
        raise NotImplementedError("audio_stream lands with STT")

    def send_video(self, bot_id: str, device: str) -> None:
        raise NotImplementedError(
            "send_video unused; relay uses voice_agent_settings.url"
        )

    @staticmethod
    def _bot(data: dict) -> Bot:
        raw = str(data.get("state", "joining"))
        mapped = _STATE_MAP.get(raw)
        if mapped is None:
            if "joined" in raw:
                mapped = "joined"
            elif raw == "fatal_error":
                mapped = "fatal_error"
            else:
                mapped = "joining"
        return Bot(id=str(data["id"]), state=mapped, raw_state=raw)
