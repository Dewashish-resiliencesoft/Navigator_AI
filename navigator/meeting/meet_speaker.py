"""Speaker that logs Meet talk; LiveAgent owns the actual mouth."""

from __future__ import annotations

import threading
from collections.abc import Callable

from navigator.meeting.attendee import AttendeeClient
from navigator.meeting.playback_handle import PlaybackHandle
from navigator.voice.tts import Speaker


class MeetSpeaker:
    """Glue: last_spoken, optional chat, stop. Audio is Live PCM, not WAV."""

    def __init__(
        self,
        local: Speaker,
        attendee: AttendeeClient,
        bot_id: str,
        *,
        also_chat: bool = False,
        after_speak: Callable[[], None] | None = None,
        set_avatar_state: Callable[[str], None] | None = None,
        check_barge_in: Callable[[], bool] | None = None,
    ) -> None:
        self.local = local
        self.attendee = attendee
        self.bot_id = bot_id
        self.also_chat = also_chat
        self.after_speak = after_speak
        self.set_avatar_state = set_avatar_state
        self.check_barge_in = check_barge_in
        self.last_spoken = ""
        self.interrupted = False
        self.bot_ended = False
        self.stop_event: threading.Event | None = None

    def set_language(self, lang: str) -> None:
        from navigator.voice.language import apply_to_speakers

        apply_to_speakers(lang, self.local)  # type: ignore[arg-type]
        print(f"[speak] language → {lang}", flush=True)

    def say(self, text: str, *, language: str | None = None) -> None:
        if language:
            self.set_language(language)
        print(f"[speak] {text}", flush=True)
        self.last_spoken = text
        self.interrupted = False
        if self.also_chat and text.strip():
            try:
                self.attendee.send_chat(self.bot_id, text)
            except Exception as exc:  # noqa: BLE001
                print(f"[speak] Meet chat failed: {exc}", flush=True)

    def say_async(self, text: str) -> PlaybackHandle:
        """Fire-and-forget log; Live path patches this via _own_meet_tts_when_live."""
        handle = PlaybackHandle()
        handle._thread = threading.Thread(
            target=self._speak_async_worker,
            args=(text, handle),
            daemon=True,
        )
        handle._thread.start()
        return handle

    def _speak_async_worker(self, text: str, handle: PlaybackHandle) -> None:
        try:
            if handle.cancelled:
                return
            self.say(text)
        except Exception as exc:  # noqa: BLE001
            handle.error = str(exc)
        finally:
            handle._finish()
