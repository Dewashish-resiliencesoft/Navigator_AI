"""Attendee API client -- meeting bot for Zoom / Google Meet."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BotState = Literal["joining", "joined", "leaving", "ended", "fatal_error"]

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


class ParticipantWaitStopped(Exception):
    """stop_event was set while waiting for a human join."""


def _is_join_event(event_type: str) -> bool:
    t = (event_type or "").lower()
    return t in {"join", "joined", "participant_join", "participant_joined"}


def _is_leave_event(event_type: str) -> bool:
    t = (event_type or "").lower()
    return t in {"leave", "left", "participant_leave", "participant_left"}


@dataclass
class Bot:
    id: str
    state: BotState
    raw_state: str = ""


@dataclass(frozen=True)
class ParticipantEvent:
    name: str
    event_type: str
    is_host: bool = False


class AttendeeClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._audio_hubs: dict[str, Any] = {}

    def _request(
        self, method: str, path: str, body: dict | None = None
    ) -> dict | list:
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
            if e.code == 400 and "Voice agents are not enabled" in detail:
                raise RuntimeError(
                    "Attendee voice agents are disabled. Run from Navigator repo:\n"
                    "  ./scripts/sync-attendee-compose.sh\n"
                    "  cd ~/projects/attendee && docker compose -f dev.docker-compose.yaml "
                    "-f local.docker-compose.yaml --profile webpage-streamer up -d --force-recreate\n"
                    f"Original: {detail}"
                ) from e
            raise RuntimeError(f"Attendee {method} {path} -> {e.code}: {detail}") from e
        except URLError as e:
            raise RuntimeError(f"Attendee unreachable at {self.base_url}: {e}") from e
        if not raw.strip():
            return {}
        return json.loads(raw)

    def join(
        self,
        meeting_url: str,
        bot_name: str = "Navigator AI",
        voice_agent_url: str | None = None,
        screenshare_url: str | None = None,
        *,
        reserve_voice_agent: bool = False,
        join_chat_message: str | None = None,
        audio_websocket_url: str | None = None,
        audio_sample_rate: int = 16000,
        google_meet_use_login: bool = False,
        zoom_tokens_url: str | None = None,
        zoom_sdk: str | None = None,
    ) -> Bot:
        """Join a meeting.

        Prefer `reserve_voice_agent=True` with no screenshare to join quietly,
        then call `enable_screenshare` after intake. Attendee rejects url +
        screenshare_url together.

        For Zoom host join, pass ``zoom_tokens_url`` (Attendee POSTs for a ZAK).
        """
        payload: dict[str, Any] = {"meeting_url": meeting_url, "bot_name": bot_name}
        if screenshare_url:
            payload["voice_agent_settings"] = {"screenshare_url": screenshare_url}
        elif voice_agent_url:
            payload["voice_agent_settings"] = {"url": voice_agent_url}
        elif reserve_voice_agent:
            # Reserve so PATCH can start screenshare mid-call.
            payload["voice_agent_settings"] = {"reserve_resources": True}

        if audio_websocket_url:
            # Mixed meeting audio (includes others speaking). Bot TTS echo filtered later.
            payload["websocket_settings"] = {
                "audio": {
                    "url": audio_websocket_url,
                    "sample_rate": audio_sample_rate,
                }
            }

        if google_meet_use_login:
            payload["google_meet_settings"] = {
                "use_login": True,
                "login_mode": "always",
            }

        if zoom_tokens_url:
            payload["callback_settings"] = {"zoom_tokens_url": zoom_tokens_url}

        # Voice agent / screenshare on Zoom needs web SDK. Caller must pass
        # zoom_sdk="web" explicitly — auto-forcing it hangs Attendee joins.
        if zoom_sdk:
            payload.setdefault("zoom_settings", {})["sdk"] = zoom_sdk

        if join_chat_message:
            payload["bot_chat_message"] = {
                "message": join_chat_message,
                "to": "everyone",
            }
        return self._bot(self._request("POST", "/bots", payload))

    def get(self, bot_id: str) -> Bot:
        return self._bot(self._request("GET", f"/bots/{bot_id}"))

    def leave(self, bot_id: str) -> None:
        self._request("POST", f"/bots/{bot_id}/leave", {})

    def enable_screenshare(self, bot_id: str, screenshare_url: str, voice_agent_url: str | None = None) -> None:
        """Start screen share mid-call (requires reserve_resources at join).

        Works for Google Meet and Zoom (Zoom needs ``zoom_sdk="web"`` at join).
        Attendee rejects ``url`` + ``screenshare_url`` in one PATCH — only send
        screenshare here. Use ``set_voice_agent_url`` for the avatar tile.
        """
        # voice_agent_url kept for call-compat; never sent (API mutual exclusion).
        _ = voice_agent_url
        self._request(
            "PATCH",
            f"/bots/{bot_id}/voice_agent_settings",
            {"screenshare_url": screenshare_url},
        )

    def set_voice_agent_url(self, bot_id: str, url: str) -> None:
        """Point bot camera tile at avatar page (mutually exclusive with screenshare)."""
        self._request(
            "PATCH",
            f"/bots/{bot_id}/voice_agent_settings",
            {"url": url},
        )

    def send_chat(self, bot_id: str, message: str, *, to: str = "everyone") -> None:
        self._request(
            "POST",
            f"/bots/{bot_id}/send_chat_message",
            {"message": message, "to": to},
        )

    def participant_events(self, bot_id: str) -> list[ParticipantEvent]:
        data = self._request("GET", f"/bots/{bot_id}/participant_events")
        rows: list
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = data.get("results") or data.get("participant_events") or []
        else:
            rows = []
        out: list[ParticipantEvent] = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            out.append(
                ParticipantEvent(
                    name=str(raw.get("participant_name") or raw.get("name") or ""),
                    event_type=str(raw.get("event_type") or raw.get("type") or ""),
                    is_host=bool(raw.get("participant_is_host") or raw.get("is_host")),
                )
            )
        return out

    def wait_for_human_join(
        self,
        bot_id: str,
        *,
        timeout_s: float = 300.0,
        poll_s: float = 3.0,
        stop_event=None,
    ) -> str:
        """Block until a non-bot participant join event appears. Returns their name."""
        deadline = time.time() + timeout_s
        seen_joins: set[str] = set()
        while time.time() < deadline:
            if stop_event is not None and stop_event.is_set():
                raise ParticipantWaitStopped("ended by operator")
            for ev in self.participant_events(bot_id):
                if not _is_join_event(ev.event_type):
                    continue
                key = ev.name or "participant"
                if key in seen_joins:
                    continue
                seen_joins.add(key)
                return ev.name or "there"
            time.sleep(poll_s)
        raise TimeoutError(
            f"no human joined Meet within {timeout_s}s (bot {bot_id})"
        )

    def human_has_left(
        self,
        bot_id: str,
        *,
        human_name: str,
        bot_names: frozenset[str] | None = None,
    ) -> bool:
        """True once the joined human's leave event is seen (event log)."""
        want = (human_name or "").strip().lower()
        bots = {n.lower() for n in (bot_names or frozenset())}
        bots.update({"navigator", "navigator ai", "attendee"})
        present: dict[str, bool] = {}
        for ev in self.participant_events(bot_id):
            name = (ev.name or "").strip()
            if not name:
                continue
            low = name.lower()
            if low in bots:
                continue
            if _is_join_event(ev.event_type):
                present[low] = True
            elif _is_leave_event(ev.event_type):
                present[low] = False
        if want and want in present:
            return present[want] is False
        # Fallback: every tracked human has left (or none remain in).
        humans = [v for k, v in present.items() if k not in bots]
        return bool(humans) and not any(humans)

    def speak(self, bot_id: str, wav: bytes) -> None:
        """Play audio into the meeting via Attendee output_audio (MP3)."""
        import base64

        mp3 = _wav_bytes_to_mp3(wav)
        self._request(
            "POST",
            f"/bots/{bot_id}/output_audio",
            {"type": "audio/mp3", "data": base64.b64encode(mp3).decode()},
        )

    def register_audio_hub(self, bot_id: str, frames_queue: Any) -> None:
        """Attach an inbound PCM queue for audio_stream (filled by AudioBridge)."""
        self._audio_hubs[bot_id] = frames_queue

    def audio_stream(self, bot_id: str, *, timeout_s: float | None = 30.0):
        """Yield PCM frames Attendee pushed for this bot."""
        import queue as queue_mod

        hubs = getattr(self, "_audio_hubs", {})
        q = hubs.get(bot_id)
        if q is None:
            raise RuntimeError(
                f"no audio hub for {bot_id}; register_audio_hub + AudioBridge first"
            )
        while True:
            try:
                if timeout_s is None:
                    yield q.get()
                else:
                    yield q.get(timeout=timeout_s)
            except queue_mod.Empty:
                return

    def send_video(self, bot_id: str, device: str) -> None:
        raise NotImplementedError("send_video unused; use enable_screenshare")

    @staticmethod
    def _bot(data: dict | list) -> Bot:
        if not isinstance(data, dict):
            raise RuntimeError(f"expected bot object, got {type(data)}")
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


def _wav_bytes_to_mp3(wav: bytes) -> bytes:
    """Convert WAV → MP3 for Attendee output_audio. Needs ffmpeg on PATH."""
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    if wav[:3] == b"ID3" or wav[:2] == b"\xff\xfb":
        return wav  # already mp3-ish
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg required to convert Piper WAV → MP3 for Meet speak")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.wav"
        dst = Path(tmp) / "out.mp3"
        src.write_bytes(wav)
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "4",
                str(dst),
            ],
            capture_output=True,
        )
        if proc.returncode != 0 or not dst.exists():
            raise RuntimeError(
                f"ffmpeg wav→mp3 failed: {proc.stderr[-400:].decode(errors='replace')}"
            )
        return dst.read_bytes()
