"""Bidirectional Gemini Live session for the live meeting demo.

The demo's LangGraph walkthrough stays in charge of *what happens*; this session
owns *the voice*. It streams meeting audio in continuously, streams the model's
audio straight back out to Attendee, and cuts playback the moment the model
reports that a human talked over it.

Threading: the rest of the live demo is synchronous (Playwright, LangGraph,
``websockets.sync``), so the asyncio session lives on one daemon thread and is
driven through queues — the same seam ``_GeminiLiveEngine`` already uses.

Three coroutines run under one gather, deliberately independent:

    _pump_in    AudioBridge.inbound -> send_realtime_input
    _pump_out   session.receive()   -> AudioBridge.push_outbound_pcm
    _pump_cmd   say/context/close   -> session

If they were one loop, a slow turn would stall the microphone — which is the
latency problem this whole path exists to remove.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from navigator.voice.language import SpokenLanguage, language_code

DEFAULT_MODEL = "gemini-3.1-flash-live-preview"
DEFAULT_VOICE = "Sulafat"
#: Gemini Live always emits 24 kHz PCM, and Attendee accepts 24 kHz directly —
#: so nothing on this path resamples.
OUTPUT_SAMPLE_RATE = 24_000
#: Attendee's mixed meeting audio.
INPUT_SAMPLE_RATE = 16_000
#: Longest ``say`` will wait for queued audio to finish playing. A stuck or
#: mis-scaled counter must not be able to stall the walkthrough.
MAX_DRAIN_S = 10.0

SayMode = Literal["verbatim", "natural"]


@dataclass
class LiveEvent:
    """Something the session noticed that the demo director may care about."""

    kind: Literal["said", "heard", "interrupted", "turn_complete", "error"]
    text: str = ""


@dataclass
class _Cmd:
    kind: Literal["say", "nudge", "context", "close"]
    text: str = ""
    mode: SayMode = "verbatim"


@dataclass
class LiveAgentConfig:
    api_key: str
    system_instruction: str
    model: str = DEFAULT_MODEL
    voice_name: str = DEFAULT_VOICE
    language: SpokenLanguage = "en"
    #: Silence before Live ends the human's turn. Google's own default is ~800ms;
    #: below ~300ms mid-sentence pauses get treated as end-of-turn.
    vad_silence_ms: int = 400
    vad_prefix_padding_ms: int = 20
    on_event: Callable[[LiveEvent], None] | None = None
    #: Extra fields for LiveConnectConfig, for forward-compat with preview flags.
    extra_config: dict[str, Any] = field(default_factory=dict)


class LiveAgent:
    """A running Gemini Live conversation wired to the meeting's audio bridge."""

    def __init__(self, cfg: LiveAgentConfig, bridge: Any) -> None:
        self.cfg = cfg
        self.bridge = bridge
        self.interrupted = False
        self.speaking = False
        self.last_spoken = ""
        self.bot_ended = False

        self._cmds: queue.Queue[_Cmd] = queue.Queue()
        self._heard: queue.Queue[str] = queue.Queue()
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._failed: str | None = None
        self._stop = threading.Event()
        self._session: Any = None
        self._resumption_handle: str | None = None
        self._turn_done = threading.Event()
        self._turn_done.set()
        self._thread: threading.Thread | None = None

    # ---- lifecycle ----------------------------------------------------

    def start(self, *, timeout_s: float = 15.0) -> bool:
        """Open the session. False means the caller should fall back to TTS."""
        self._thread = threading.Thread(
            target=self._run_loop, name="gemini-live-agent", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=timeout_s):
            self._failed = self._failed or f"session did not open within {timeout_s}s"
        if self._failed:
            print(f"[live] Gemini Live unavailable: {self._failed}", flush=True)
            return False
        print(f"[live] Gemini Live session open ({self.cfg.model})", flush=True)
        return True

    def close(self) -> None:
        self._stop.set()
        self._cmds.put(_Cmd(kind="close"))
        if self._thread is not None:
            self._thread.join(timeout=5)
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except RuntimeError:
            pass

    # ---- director API -------------------------------------------------

    def say(self, text: str, *, mode: SayMode = "verbatim") -> None:
        """Speak a scripted line and block until the meeting has heard it.

        Matches ``MeetSpeaker.say`` so SPEAKING can call either one.
        """
        if not (text or "").strip():
            return
        self.last_spoken = text
        self.interrupted = False
        self._turn_done.clear()
        started = time.monotonic()
        sent_at_start = self._audio_s_sent()
        self._cmds.put(_Cmd(kind="say", text=text, mode=mode))
        print(f"[speak] {text}", flush=True)
        # Generous ceiling: this is a stuck-session guard, not pacing. Normal
        # turns end on turn_complete or on the human interrupting.
        self._turn_done.wait(timeout=90)
        self._wait_for_playback(started, sent_at_start)

    def _audio_s_sent(self) -> float:
        """Seconds of bot audio the bridge has handed to the meeting so far."""
        return float(getattr(self.bridge, "audio_s_sent", 0.0) or 0.0)

    def _wait_for_playback(self, started: float, sent_at_start: float) -> None:
        """Hold until the audio for this turn has had time to play.

        ``turn_complete`` only means the model stopped *generating*. Attendee
        and the browser are still several buffers behind it, so returning here
        would let EXECUTING click while the line is still being heard — and the
        gap widens with every sentence.
        """
        if self.interrupted:
            # Barge-in flushed the queue; that audio will never play.
            return
        slack = self._audio_s_sent() - sent_at_start - (time.monotonic() - started)
        if slack > 0:
            self._stop.wait(min(slack, MAX_DRAIN_S))

    def nudge(self, text: str) -> None:
        """Fire-and-forget short ack. Does not wait for turn_complete.

        Used while the director runs browser work so the call never goes dead.
        """
        if not (text or "").strip():
            return
        self.last_spoken = text
        self._cmds.put(_Cmd(kind="nudge", text=text))
        print(f"[speak] nudge: {text}", flush=True)

    def add_context(self, text: str) -> None:
        """Tell the model what just happened on screen. Never spoken aloud."""
        if (text or "").strip():
            self._cmds.put(_Cmd(kind="context", text=text))

    def set_language(self, lang: SpokenLanguage) -> None:
        """Switch spoken language for later turns (soft — no session reconnect).

        ``speech_config.language_code`` is fixed at connect; we update cfg +
        inject a hard context so the model stops refusing and answers in-lang.
        """
        if lang == self.cfg.language:
            return
        self.cfg.language = lang
        label = "Hindi" if lang == "hi" else "English"
        self.add_context(
            f"The person asked you to speak {label} from now on. Speak only "
            f"{label} until told otherwise. Never refuse a language switch and "
            f"never say you can only speak another language in this demo. "
            f"Acknowledge briefly in {label}, then continue."
        )
        print(f"[speak] live language → {lang}", flush=True)

    def wait_until_idle(self, *, silence_s: float, timeout_s: float = 30.0) -> None:
        """Block until the model has been quiet for `silence_s`.

        Used after an interruption so the walkthrough resumes only once the
        person and the agent have actually finished their exchange.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._turn_done.wait(timeout=0.1) and not self.speaking:
                quiet_until = time.monotonic() + silence_s
                while time.monotonic() < quiet_until:
                    if self.speaking:
                        break
                    time.sleep(0.05)
                else:
                    return

    def wait_for_heard(self, *, timeout_s: float = 30.0) -> str:
        """Next prospect utterance from Live input transcription, or \"\"."""
        deadline = time.monotonic() + max(0.0, timeout_s)
        while time.monotonic() < deadline:
            try:
                text = self._heard.get(timeout=0.1)
            except queue.Empty:
                if self._stop.is_set():
                    return ""
                continue
            text = (text or "").strip()
            if text:
                return text
        return ""

    def drain_heard(self) -> None:
        """Drop buffered transcripts (e.g. before an intake question)."""
        while True:
            try:
                self._heard.get_nowait()
            except queue.Empty:
                return

    # ---- asyncio side -------------------------------------------------

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as exc:  # noqa: BLE001
            self._failed = self._failed or str(exc)
            self._emit(LiveEvent(kind="error", text=str(exc)))
        finally:
            self._ready.set()
            self._turn_done.set()

    def _build_config(self) -> Any:
        from google.genai import types

        return types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=self.cfg.system_instruction,
            speech_config=types.SpeechConfig(
                language_code=language_code(self.cfg.language),
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.cfg.voice_name,
                    ),
                ),
            ),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                    prefix_padding_ms=self.cfg.vad_prefix_padding_ms,
                    silence_duration_ms=self.cfg.vad_silence_ms,
                ),
            ),
            thinking_config=types.ThinkingConfig(
                thinking_level=types.ThinkingLevel.MINIMAL,
            ),
            # Native-audio models only emit audio, so transcription is the only
            # way to see text — needed for the transcript and for echo checks.
            output_audio_transcription=types.AudioTranscriptionConfig(),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            # Demos outlive the 15-minute audio-only session cap.
            session_resumption=types.SessionResumptionConfig(
                handle=self._resumption_handle,
            ),
            **self.cfg.extra_config,
        )

    async def _main(self) -> None:
        from google import genai

        client = genai.Client(api_key=self.cfg.api_key)
        while not self._stop.is_set():
            try:
                async with client.aio.live.connect(
                    model=self.cfg.model, config=self._build_config()
                ) as session:
                    self._session = session
                    self._ready.set()
                    if self._resumption_handle:
                        print("[live] Gemini Live session resumed", flush=True)
                    await asyncio.gather(
                        self._pump_in(session),
                        self._pump_out(session),
                        self._pump_cmd(session),
                    )
            except Exception as exc:  # noqa: BLE001
                if self._stop.is_set():
                    return
                self._failed = self._failed or str(exc)
                self._emit(LiveEvent(kind="error", text=f"session: {exc}"))
                self._ready.set()
            if self._stop.is_set() or not self._resumption_handle:
                return
            print("[live] Live session dropped — reconnecting", flush=True)
            await asyncio.sleep(0.5)

    async def _pump_in(self, session: Any) -> None:
        """Meeting audio -> model, continuously. Never batched, never buffered."""
        while not self._stop.is_set():
            try:
                pcm = await asyncio.to_thread(self.bridge.inbound.get, True, 0.2)
            except queue.Empty:
                continue
            except Exception:  # noqa: BLE001
                continue
            try:
                await session.send_realtime_input(
                    audio={
                        "data": pcm,
                        "mime_type": f"audio/pcm;rate={INPUT_SAMPLE_RATE}",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                self._emit(LiveEvent(kind="error", text=f"send audio: {exc}"))
                return

    async def _pump_out(self, session: Any) -> None:
        """Model audio -> Attendee, chunk by chunk. No whole-turn buffering."""
        while not self._stop.is_set():
            try:
                async for msg in session.receive():
                    if self._stop.is_set():
                        return
                    self._handle_server_message(msg)
            except Exception as exc:  # noqa: BLE001
                if self._stop.is_set():
                    return
                self._emit(LiveEvent(kind="error", text=f"receive: {exc}"))
                return

    def _handle_server_message(self, msg: Any) -> None:
        update = getattr(msg, "session_resumption_update", None)
        if update is not None and getattr(update, "new_handle", None):
            self._resumption_handle = update.new_handle
        if getattr(msg, "go_away", None) is not None:
            print("[live] server go_away — will resume", flush=True)

        sc = getattr(msg, "server_content", None)

        if sc is not None and getattr(sc, "interrupted", False):
            # The model stopped generating because a human spoke. Anything
            # still queued downstream is now stale and must not play.
            self.interrupted = True
            self.speaking = False
            self.bridge.flush_bot_output()
            self._turn_done.set()
            self._emit(LiveEvent(kind="interrupted"))
            return

        # One ServerContent can carry several parts (audio plus transcript).
        model_turn = getattr(sc, "model_turn", None) if sc is not None else None
        for part in getattr(model_turn, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline is not None else None
            if data:
                self.speaking = True
                self.bridge.push_outbound_pcm(
                    data, sample_rate=OUTPUT_SAMPLE_RATE
                )

        # `msg.data` is the SDK's flattened view of the same audio; only use it
        # when parts gave us nothing, or the audio plays twice.
        if not (getattr(model_turn, "parts", None) or []) and getattr(msg, "data", None):
            self.speaking = True
            self.bridge.push_outbound_pcm(msg.data, sample_rate=OUTPUT_SAMPLE_RATE)

        if sc is not None:
            out = getattr(sc, "output_transcription", None)
            if out is not None and getattr(out, "text", ""):
                self._emit(LiveEvent(kind="said", text=out.text))
            heard = getattr(sc, "input_transcription", None)
            if heard is not None and getattr(heard, "text", ""):
                text = heard.text
                try:
                    self._heard.put_nowait(text)
                except queue.Full:
                    pass
                self._emit(LiveEvent(kind="heard", text=text))
            if getattr(sc, "turn_complete", False):
                self.speaking = False
                self._turn_done.set()
                self._emit(LiveEvent(kind="turn_complete"))

    async def _pump_cmd(self, session: Any) -> None:
        while not self._stop.is_set():
            try:
                cmd = await asyncio.to_thread(self._cmds.get, True, 0.2)
            except queue.Empty:
                continue
            except Exception:  # noqa: BLE001
                continue
            if cmd.kind == "close":
                return
            try:
                await session.send_realtime_input(
                    text=_prompt_for(cmd, language=self.cfg.language)
                )
            except Exception as exc:  # noqa: BLE001
                self._emit(LiveEvent(kind="error", text=f"send text: {exc}"))
                self._turn_done.set()
                return

    def _emit(self, event: LiveEvent) -> None:
        if self.cfg.on_event is None:
            return
        try:
            self.cfg.on_event(event)
        except Exception as exc:  # noqa: BLE001
            print(f"[live] event handler failed: {exc}", flush=True)


def _prompt_for(cmd: _Cmd, *, language: SpokenLanguage = "en") -> str:
    """Turn a director command into an instruction the model will not read aloud."""
    lang_hint = " in Hindi" if language == "hi" else " in English"
    if cmd.kind == "context":
        return (
            "[Context, do not say this out loud and do not acknowledge it] "
            f"{cmd.text}"
        )
    if cmd.kind == "nudge":
        return (
            f"[Say this brief working ack aloud once{lang_hint}, then stop. Do not "
            f"elaborate or narrate what you are doing] {cmd.text}"
        )
    if cmd.mode == "natural":
        return (
            f"[Say the following to the person now{lang_hint}, in your own words, "
            f"in one or two sentences, then stop] {cmd.text}"
        )
    return (
        f"[Say the following to the person now{lang_hint}, word for word, then stop] "
        f"{cmd.text}"
    )
