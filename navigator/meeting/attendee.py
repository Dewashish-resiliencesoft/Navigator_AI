"""Attendee API client -- the meeting bot that joins Zoom and Google Meet.

Attendee is an external service (Django + Postgres + Redis, self-hosted via
Docker). This module only talks to its REST API; we build none of it.

STUB. Phase 3 fills this in.

Setup notes for whoever does:
  - Zoom needs a Zoom OAuth app (General App, Meeting SDK enabled) plus a
    Deepgram key for transcription; 400h free on Deepgram.
  - Google Meet needs no Google credentials -- Attendee drives a real Chrome.
  - Media send exists: bots can speak arbitrary audio and display an image via a
    virtual webcam. Confirm the current endpoint shapes against docs.attendee.dev
    before writing the calls; the public README documents only /bots,
    /bots/<id>, and /bots/<id>/transcript.
  - Attendee's roadmap includes streaming an arbitrary website into a meeting.
    If that ships, it likely replaces the v4l2loopback + ffmpeg pipeline
    entirely -- check before building that subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BotState = Literal["joining", "joined", "leaving", "ended", "fatal_error"]


@dataclass
class Bot:
    id: str
    state: BotState


class AttendeeClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def join(self, meeting_url: str, bot_name: str = "Navigator AI") -> Bot:
        # TODO(phase 3): POST /bots {meeting_url, bot_name} -> Bot
        raise NotImplementedError("Attendee integration lands in Phase 3")

    def get(self, bot_id: str) -> Bot:
        # TODO(phase 3): GET /bots/<id>
        raise NotImplementedError("Attendee integration lands in Phase 3")

    def leave(self, bot_id: str) -> None:
        # TODO(phase 3): POST /bots/<id>/leave
        raise NotImplementedError("Attendee integration lands in Phase 3")

    def speak(self, bot_id: str, wav: bytes) -> None:
        """Play audio into the meeting -- the output half of SPEAKING."""
        # TODO(phase 3): send Piper's wav via the bot output-audio endpoint.
        raise NotImplementedError("Attendee integration lands in Phase 3")

    def audio_stream(self, bot_id: str):
        """Inbound participant audio, for LISTENING's VAD."""
        # TODO(phase 3): websocket audio in; yield PCM frames.
        raise NotImplementedError("Attendee integration lands in Phase 3")

    def send_video(self, bot_id: str, device: str) -> None:
        """Point the bot's camera at the rendered browser."""
        # TODO(phase 3): v4l2loopback device fed by ffmpeg capturing the
        # Playwright viewport -- unless Attendee's website-streaming feature
        # makes this unnecessary.
        raise NotImplementedError("Attendee integration lands in Phase 3")
