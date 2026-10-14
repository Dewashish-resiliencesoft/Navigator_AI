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

import array
import asyncio
import os
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
LIVE_CONNECT_ATTEMPTS = 3

SayMode = Literal["verbatim", "natural", "acknowledge", "result", "error"]


def should_retry_live_connect(
    exc: BaseException, attempt: int, *, max_attempts: int = LIVE_CONNECT_ATTEMPTS
) -> bool:
    """Retry Gemini Live 1011 / overload. Permanent errors (bad key) stay fatal."""
    from navigator.core.gemini_keys import is_gemini_live_unavailable

    return attempt < max_attempts and is_gemini_live_unavailable(exc)


def _unspoken_tail(line: str, said: str) -> str:
    """Words of ``line`` the model never said when it covered < 40% of them."""
    import re

    words = (line or "").split()
    if len(words) < 7:
        return ""
    norm = lambda w: re.sub(r"[^a-z0-9]", "", w.lower())  # noqa: E731
    said_words = {norm(w) for w in (said or "").split()} - {""}
    if not said_words:
        return ""  # no transcript at all: cannot judge, don't double-speak
    hit = sum(1 for w in words if norm(w) in said_words)
    if hit / len(words) >= 0.4:
        return ""
    last = 0
    for i, w in enumerate(words):
        if norm(w) in said_words:
            last = i + 1
    return " ".join(words[last:])


def _is_real_speech(text: str) -> bool:
    """Real words, not STT noise like 'e' / 'uh' / '.'."""
    alnum = "".join(ch for ch in (text or "") if ch.isalnum())
    if len(alnum) < 3:
        return False
    return alnum.lower() not in {"uhh", "umm", "hmm", "mmm", "ahh", "ehh"}



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
    vad_silence_ms: int = 800
    vad_prefix_padding_ms: int = 20
    on_event: Callable[[LiveEvent], None] | None = None
    #: Screen tools (show_feature). Handler runs on a worker thread and blocks
    #: until the demo thread executes the job; ui_pump lets waits service jobs.
    tool_handler: Callable[[str, dict], dict] | None = None
    tool_declarations: list[dict[str, Any]] = field(default_factory=list)
    ui_pump: Callable[[], Any] | None = None
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
        #: When True, keep transcribing the human but drop the model's own audio
        #: so it doesn't talk over / answer itself (used during intake Q&A).
        self.listen_only = False
        #: Playlist walkthrough: do not feed Meet mic into Live (self-echo)
        #: and ignore VAD barge-in. Director say()/nudge() still speak.
        #: Start in director_only=True — the demo script controls all speech
        #: until intake is done. Only set to False when real Q&A begins.
        self.director_only = True
        #: Playback: ready → synthesizing → buffering → playing → draining → finished
        #: Interrupt: playing → interrupted → stopped → cleanup → ready
        self.playback_phase: str = "ready"
        self._active_utterance_id: str | None = None
        self._playback_idle = threading.Event()
        self._playback_idle.set()
        self._barge_state: Literal["idle", "probable", "confirmed"] = "idle"
        self._barge_probable_at: float = 0.0
        # Was 0.28s: room echo / a click sound right after login confirmed a
        # fake barge-in. Real interruptions keep talking well past this.
        self._barge_confirm_s: float = 0.8
        #: When the model finished generating the last turn and how long its
        #: audio still needs to play (lets wait_idle recover from a stuck
        #: "playing" phase left by nudges/interrupted turns).
        self._gen_done_at: float = 0.0
        self._drain_until: float = 0.0

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
        # After a confirmed barge-in, ignore extra interrupted events briefly.
        self._last_interrupt_time: float = 0.0
        self._interrupt_cooldown_s: float = 1.5
        # Metrics
        self.barge_in_detected: int = 0
        self.barge_in_confirmed: int = 0
        self.barge_in_rejected_cooldown: int = 0
        self.audio_flush_count: int = 0

    # ---- lifecycle ----------------------------------------------------

    def start(self, *, timeout_s: float = 15.0) -> bool:
        """Open the session. False means the caller should stop the demo."""
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
        # Stopping the loop mid run_until_complete raised "Event loop stopped
        # before Future completed". Cancel the main task instead.
        if self._thread is not None and self._thread.is_alive():
            def _cancel_all() -> None:
                for t in asyncio.all_tasks(self._loop):
                    t.cancel()
            try:
                self._loop.call_soon_threadsafe(_cancel_all)
            except RuntimeError:
                pass
            self._thread.join(timeout=3)

    # ---- director API -------------------------------------------------

    def can_start_utterance(self) -> bool:
        """True when a new TTS turn will not overlap in-flight audio."""
        if self._barge_state == "confirmed" and self.interrupted:
            return True
        return self.playback_phase in {"ready", "finished", "stopped"} and (
            self._playback_idle.is_set()
        )

    def _set_playback(
        self, phase: str, utterance_id: str | None, *, reason: str = ""
    ) -> None:
        self.playback_phase = phase
        if utterance_id is not None:
            self._active_utterance_id = utterance_id
        extra = f" reason={reason}" if reason else ""
        print(
            f"[utterance] id={self._active_utterance_id} playback={phase} "
            f"queue={self._cmds.qsize()}{extra}",
            flush=True,
        )
        if phase in {"ready", "finished", "stopped"}:
            self._playback_idle.set()
        elif phase in {"synthesizing", "buffering", "playing", "draining"}:
            self._playback_idle.clear()

    def _confirm_barge_in(self, reason: str = "user") -> None:
        """Confirmed barge-in: flush Attendee audio, then allow a new utterance."""
        uid = self._active_utterance_id
        self._barge_state = "confirmed"
        self.interrupted = True
        self.speaking = False
        self.barge_in_confirmed += 1
        self._last_interrupt_time = time.monotonic()
        self._set_playback("interrupted", uid, reason=reason)
        try:
            self.bridge.flush_bot_output()
        except Exception:  # noqa: BLE001
            pass
        self.audio_flush_count += 1
        self._turn_done.set()
        self._set_playback("stopped", uid, reason=reason)
        self._set_playback("cleanup", uid, reason=reason)
        self._set_playback("ready", uid, reason=reason)
        self._emit(LiveEvent(kind="interrupted"))
        print(
            f"[utterance] id={uid} interruption={reason} "
            f"detected={self.barge_in_detected} confirmed={self.barge_in_confirmed}",
            flush=True,
        )

    def _note_probable_barge(self) -> None:
        now = time.monotonic()
        self.barge_in_detected += 1
        self._barge_state = "probable"
        self._barge_probable_at = now
        print(
            f"[utterance] id={self._active_utterance_id} barge=probable "
            f"confirm_window={self._barge_confirm_s}s",
            flush=True,
        )

    def say(
        self, text: str, *, mode: SayMode = "verbatim", utterance_id: str | None = None
    ) -> None:
        """Speak a scripted line and block until the meeting has heard it.

        Matches ``MeetSpeaker.say`` so SPEAKING can call either one.
        """
        if not (text or "").strip():
            return
        uid = utterance_id or ""
        key = " ".join(text.lower().split())
        spoken = self.__dict__.setdefault("_spoken_keys", set())
        if mode == "verbatim" and len(key) > 20 and key in spoken:
            print(f"[utterance] id={uid} skip replay of already-spoken line", flush=True)
            return
        spoken.add(key)
        if not self.can_start_utterance():
            print(
                f"[utterance] id={uid} wait_idle phase={self.playback_phase} "
                f"queue={self._cmds.qsize()}",
                flush=True,
            )
            self._wait_idle_or_recover(uid)
        if self._barge_state == "confirmed":
            self._barge_state = "idle"
        self.last_spoken = text
        self.interrupted = False
        self.speaking = True
        self._turn_done.clear()
        self._set_playback("synthesizing", uid or None)
        started = time.monotonic()
        sent_at_start = self._audio_s_sent()
        self._turn_said = []
        self._in_say = True
        self._say_queued_at = time.monotonic()
        self._cmds.put(_Cmd(kind="say", text=text, mode=mode))
        print(f"[speak] {text}", flush=True)
        self._wait_turn_with_watchdog(text, mode, uid, 90)
        rest = _unspoken_tail(text, " ".join(self._turn_said))
        if rest and max(getattr(self, "_last_heard_at", 0.0),
                        getattr(self, "_last_voice_at", 0.0)) >= started:
            # The prospect spoke during this line: Live answered them instead.
            # Re-reading the tail afterwards sounded robotic in the e2e run.
            print(f"[speak] line cut by prospect question; not replaying tail", flush=True)
            rest = ""
        if rest and not self.interrupted and not self._stop.is_set():
            # Model stopped early (last run: spoke only "let go" of a long line).
            print(f"[speak] line cut short; finishing: {rest}", flush=True)
            self._turn_said = []
            self._turn_done.clear()
            self._cmds.put(_Cmd(kind="say", text=rest, mode="verbatim"))
            self._pumped_wait(self._turn_done, 60)
        if not self.interrupted:
            self._set_playback("draining", uid or None)
        self._wait_for_playback(started, sent_at_start)
        # Always leave the pipeline idle, also after an interrupted turn —
        # otherwise the next step's say() waits on "playing" forever.
        if self.playback_phase not in {"ready", "finished", "stopped"}:
            self._set_playback("finished", uid or None)
        self._set_playback("ready", uid or None)
        self.speaking = False
        self._in_say = False

    def _wait_idle_or_recover(self, uid: str, max_wait_s: float = 15.0) -> None:
        """Wait for in-flight audio; force idle when generation already ended.

        Nudges and interrupted turns leave ``playback_phase`` at "playing"
        with nobody to move it on, which used to deadlock the walkthrough.
        """
        deadline = time.monotonic() + max_wait_s
        while not self._stop.is_set():
            if self.can_start_utterance():
                return
            now = time.monotonic()
            # Root cause of the 15s timeouts: drain_until assumed the WHOLE turn
            # starts playing at turn_complete. Use the bridge's real play-end
            # estimate; also recover when audio stopped arriving (no
            # turn_complete after a live answer).
            play_end = float(getattr(self.bridge, "_tap_play_end", 0.0) or 0.0)
            audio_over = bool(play_end) and now >= play_end + 0.5
            if audio_over and (self._gen_done_at or now >= play_end + 1.5):
                print(f"[utterance] id={uid} wait_idle recovered (audio played out)", flush=True)
                self.speaking = False
                break
            if self._gen_done_at and not self.speaking and now >= self._drain_until:
                print(f"[utterance] id={uid} wait_idle recovered (turn done, drained)", flush=True)
                break
            if now >= deadline:
                print(f"[utterance] id={uid} wait_idle timeout {max_wait_s}s -> force ready", flush=True)
                # Usually a live answer still talking: not a glitch to apologise for.
                self.last_wait_timeout_at = now
                break
            self._pump_ui()
            self._playback_idle.wait(timeout=0.25)
        self._set_playback("ready", uid or None)

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
        if self.interrupted and self._barge_state == "confirmed":
            # Confirmed barge-in flushed the queue; that audio will never play.
            return
        slack = self._audio_s_sent() - sent_at_start - (time.monotonic() - started)
        if slack > 0 and self.cfg.ui_pump is None:
            self._stop.wait(min(slack, MAX_DRAIN_S))
        elif slack > 0:
            end = time.monotonic() + min(slack, MAX_DRAIN_S)
            while time.monotonic() < end and not self._stop.is_set():
                self._pump_ui()
                self._stop.wait(min(0.1, max(0.0, end - time.monotonic())))

    def _note_first_audio(self) -> None:
        from navigator.core import logtime

        now = time.monotonic()
        jit = 250.0  # Attendee jitter buffer (patch) — audio heard ~this much later
        q = getattr(self, "_say_queued_at", 0.0)
        if q and getattr(self, "_in_say", False):
            self._say_queued_at = 0.0
            logtime.record("text_queued->first_audio", (now - q) * 1000)
            logtime.record("first_audio->heard_in_zoom_est", jit + 40)
            return
        u = getattr(self, "_await_answer_from", 0.0)
        if u and not getattr(self, "_in_say", False):
            self._await_answer_from = 0.0
            logtime.record("user_end->first_audio", (now - u) * 1000)
            logtime.record("first_audio->heard_in_zoom_est", jit + 40)

    def _pump_ui(self) -> None:
        pump = self.cfg.ui_pump
        if pump is not None:
            try:
                pump()
            except Exception as exc:  # noqa: BLE001
                print(f"[ui-tool] pump failed: {exc}", flush=True)

    def _pumped_wait(self, ev: threading.Event, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if ev.wait(timeout=0.1):
                return True
            self._pump_ui()
        return ev.is_set()

    @property
    def answering(self) -> bool:
        """Live is answering the prospect (not a scripted say) or just heard them."""
        now = time.monotonic()
        if self.speaking and not getattr(self, "_in_say", False):
            self._answer_seen_at = now
            return True
        # Resume only after the answer finished + 1.5s of silence.
        if now - getattr(self, "_answer_seen_at", 0.0) < 1.5:
            return True
        play_end = float(getattr(self.bridge, "_tap_play_end", 0.0) or 0.0)
        if (now - getattr(self, "_answer_seen_at", 0.0) < 30.0 and now < play_end + 1.5
                and not getattr(self, "_in_say", False)):
            return True
        if self.director_only:
            return False
        return (now - getattr(self, "_last_heard_at", 0.0) < 4.0
                or now - getattr(self, "_last_voice_at", 0.0) < 2.5)

    async def _run_tools(self, calls: list) -> None:
        from google.genai import types

        responses = []
        for fc in calls:
            name = getattr(fc, "name", "")
            args = dict(getattr(fc, "args", None) or {})
            print(f"[live] tool_call {name} {args}", flush=True)
            _t0 = time.monotonic()
            if getattr(self, "_in_say", False):
                # Scripted line in progress: the flow executor owns the screen.
                # A model-initiated click here raced the flow and cut the line.
                print(f"[live] tool_call {name} refused: scripted line in progress", flush=True)
                responses.append(types.FunctionResponse(
                    id=getattr(fc, "id", None), name=name,
                    response={"ok": False, "error": "Not now. You are reading a scripted line; the demo "
                              "drives the screen itself. Do not call tools. Just finish the line verbatim."}))
                continue
            try:
                res = await asyncio.to_thread(self.cfg.tool_handler, name, args)
            except Exception as exc:  # noqa: BLE001
                res = {"ok": False, "error": str(exc)[:200]}
            from navigator.core import logtime

            logtime.record("tool_call->result", (time.monotonic() - _t0) * 1000, name)
            # Answer audio latency is measured from the tool result.
            self._await_answer_from = time.monotonic()
            responses.append(types.FunctionResponse(id=getattr(fc, "id", None), name=name, response=res))
        try:
            await self._session.send_tool_response(function_responses=responses)
        except Exception as exc:  # noqa: BLE001
            self._emit(LiveEvent(kind="error", text=f"tool response: {exc}"))

    def nudge(self, text: str) -> None:
        """Fire-and-forget short ack. Does not wait for turn_complete.

        Used while the director runs browser work so the call never goes dead.
        """
        if not (text or "").strip():
            return
        if not self.can_start_utterance():
            print(
                f"[utterance] skip nudge phase={self.playback_phase} "
                f"queue={self._cmds.qsize()}",
                flush=True,
            )
            return
        self.last_spoken = text
        self._cmds.put(_Cmd(kind="nudge", text=text))
        print(f"[speak] nudge: {text}", flush=True)

    def add_context(self, text: str) -> None:
        """Tell the model what just happened on screen. Never spoken aloud."""
        if (text or "").strip():
            self._cmds.put(_Cmd(kind="context", text=text))

    def accept_inbound(self) -> bool:
        """Meet mix includes our own voice — mute mic while we are speaking.

        Block inbound audio when:
        - director_only: scripted intake/walkthrough, Live must not self-generate
        - speaking: model is producing audio (self-echo guard)
        - cmd queue non-empty: a say() was queued but model hasn't started yet
          (race guard — prevents bot audio echoing back before first audio frame)
        """
        if self.director_only:
            return False
        if getattr(self.bridge, "echo_free", False):
            # Per-participant streams never contain our own voice (Zoom sends
            # other people's tracks only), so keep listening while we talk —
            # that's what makes barge-in questions possible.
            return True
        if self.speaking:
            return False
        # If there is a pending say/nudge command that hasn't been sent to the
        # model yet, the model will start generating shortly; block the echo
        # window now rather than after the first audio packet arrives.
        if not self._cmds.empty():
            return False
        return True

    def _is_self_echo(self, heard: str) -> bool:
        if not (heard or "").strip() or not (self.last_spoken or "").strip():
            return False
        from navigator.meeting.intake_clean import is_likely_bot_echo

        return is_likely_bot_echo(heard, self.last_spoken)

    def set_director_only(self, on: bool) -> None:
        """Speak only scripted say()/nudge(); drop Meet mic until Q&A."""
        self.director_only = on
        if on:
            self.interrupted = False
            try:
                self.bridge.flush_bot_output()
            except Exception:  # noqa: BLE001
                pass
            self.drain_heard()
            inbound = getattr(self.bridge, "inbound", None)
            if inbound is not None:
                while True:
                    try:
                        inbound.get_nowait()
                    except Exception:  # noqa: BLE001
                        break
        print(f"[live] director_only={'on' if on else 'off'}", flush=True)

    def set_listen_only(self, on: bool) -> None:
        """Silence the model's mouth while still hearing the human.

        Native-audio Live auto-answers every turn it detects. During intake that
        means it replies to the human's answer (and to its own echo) — the demo
        "asks a question then answers itself". Gating outbound audio keeps the
        input transcription flowing for intake while stopping the self-answer.
        """
        self.listen_only = on
        if on:
            try:
                self.bridge.flush_bot_output()
            except Exception:  # noqa: BLE001
                pass

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
            f"Do not acknowledge this instruction out loud."
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
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            if not self._stop.is_set():
                self._failed = self._failed or str(exc)
                self._emit(LiveEvent(kind="error", text=str(exc)))
        finally:
            self._ready.set()
            self._turn_done.set()

    def _build_config(self) -> Any:
        from google.genai import types

        extra = dict(self.cfg.extra_config)
        if self.cfg.tool_handler is not None and self.cfg.tool_declarations:
            extra["tools"] = [types.Tool(function_declarations=[
                types.FunctionDeclaration(**d) for d in self.cfg.tool_declarations
            ])]
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
                    end_of_speech_sensitivity=(
                        types.EndSensitivity.END_SENSITIVITY_HIGH
                        if os.environ.get("NAVIGATOR_LIVE_EOS_HIGH", "1") == "1"
                        else None
                    ),
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
            **extra,
        )

    async def _main(self) -> None:
        from google import genai

        client = genai.Client(api_key=self.cfg.api_key)
        attempt = 0
        while not self._stop.is_set():
            try:
                async with client.aio.live.connect(
                    model=self.cfg.model, config=self._build_config()
                ) as session:
                    self._session = session
                    self._ready.set()
                    if self._resumption_handle:
                        print("[live] Gemini Live session resumed", flush=True)
                    self._reconnect_req = False
                    tasks = [
                        asyncio.ensure_future(self._pump_in(session)),
                        asyncio.ensure_future(self._pump_out(session)),
                        asyncio.ensure_future(self._pump_cmd(session)),
                        asyncio.ensure_future(self._watch_reconnect()),
                    ]
                    # gather() hung forever when one pump died (receive error,
                    # silent stall): the session was dead but nothing noticed.
                    _done, pending = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                    for t in pending:
                        t.cancel()
                    if self._reconnect_req and not self._stop.is_set():
                        print("[live] Live session stalled — reconnecting now", flush=True)
                        self._reconnect_req = False
                        await asyncio.sleep(0.3)
                        continue
            except Exception as exc:  # noqa: BLE001
                if self._stop.is_set():
                    return
                attempt += 1
                if should_retry_live_connect(exc, attempt):
                    print(
                        f"[live] Gemini Live {exc} — retry {attempt}/{LIVE_CONNECT_ATTEMPTS}",
                        flush=True,
                    )
                    await asyncio.sleep(1.5)
                    continue
                self._failed = self._failed or str(exc)
                self._emit(LiveEvent(kind="error", text=f"session: {exc}"))
                self._ready.set()
            if self._stop.is_set() or self._failed:
                return
            attempt += 1
            if attempt > LIVE_CONNECT_ATTEMPTS + 3:
                self._failed = "Live session kept dropping"
                return
            print("[live] Live session dropped — reconnecting", flush=True)
            await asyncio.sleep(0.5)

    async def _watch_reconnect(self) -> None:
        while not self._stop.is_set() and not getattr(self, "_reconnect_req", False):
            await asyncio.sleep(0.2)

    def request_reconnect(self) -> None:
        self._reconnect_req = True

    def _wait_turn_with_watchdog(self, text: str, mode: Any, uid: str, timeout: float) -> None:
        """Wait for the model turn; recover when Live goes silent.

        12:19 run: one intake line got no audio and no turn_complete; say()
        blocked the whole demo for 90s. Now: no audio in 10s -> reconnect the
        session and resend once; give up on the line after 25s."""
        start = time.monotonic()
        audio0 = self._audio_s_sent()
        retried = False
        while True:
            if self._pumped_wait(self._turn_done, 0.5):
                return
            if self._stop.is_set():
                return
            el = time.monotonic() - start
            got_audio = self._audio_s_sent() > audio0 or self.speaking
            if not got_audio and el >= 10 and not retried:
                print(f"[speak] id={uid} no audio from Live in 10s — reconnecting and resending", flush=True)
                retried = True
                self.request_reconnect()
                self._cmds.put(_Cmd(kind="say", text=text, mode=mode))
            elif not got_audio and el >= 25:
                print(f"[speak] id={uid} Live still silent after 25s — skipping line", flush=True)
                self._turn_done.set()
                return
            elif el >= timeout:
                print(f"[speak] id={uid} turn timeout {timeout:.0f}s — continuing", flush=True)
                return

    async def _pump_in(self, session: Any) -> None:
        """Meeting audio -> model, continuously. Never batched, never buffered."""
        while not self._stop.is_set():
            try:
                pcm = await asyncio.to_thread(self.bridge.inbound.get, True, 0.2)
            except queue.Empty:
                continue
            except Exception:  # noqa: BLE001
                continue
            if not self.accept_inbound():
                continue
            try:
                a = array.array("h", pcm)
                if len(a) and (sum(x * x for x in a[::8]) / max(1, len(a[::8]))) ** 0.5 > 900:
                    self._last_voice_at = time.monotonic()
            except Exception:  # noqa: BLE001
                pass
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
        tc = getattr(msg, "tool_call", None)
        if tc is not None and self.cfg.tool_handler is not None:
            calls = list(getattr(tc, "function_calls", None) or [])
            if calls:
                asyncio.ensure_future(self._run_tools(calls))
        update = getattr(msg, "session_resumption_update", None)
        if update is not None and getattr(update, "new_handle", None):
            self._resumption_handle = update.new_handle
        if getattr(msg, "go_away", None) is not None:
            print("[live] server go_away — will resume", flush=True)

        sc = getattr(msg, "server_content", None)

        if sc is not None and getattr(sc, "interrupted", False):
            if self.director_only:
                return
            now = time.monotonic()
            if self._barge_state == "confirmed":
                elapsed = now - self._last_interrupt_time
                if elapsed < self._interrupt_cooldown_s:
                    self.barge_in_rejected_cooldown += 1
                    print(
                        f"[live] barge-in suppressed (cooldown {elapsed:.2f}s "
                        f"< {self._interrupt_cooldown_s}s)",
                        flush=True,
                    )
                    return
            if self._barge_state != "probable":
                self._note_probable_barge()
                return
            if now - self._barge_probable_at < self._barge_confirm_s:
                print(
                    f"[live] barge-in still probable "
                    f"({now - self._barge_probable_at:.2f}s < {self._barge_confirm_s}s)",
                    flush=True,
                )
                return
            # VAD alone (noise, 'e') never confirms; wait for a real transcript.
            print("[live] barge-in held: waiting for real STT speech", flush=True)
            return

        # One ServerContent can carry several parts (audio plus transcript).
        model_turn = getattr(sc, "model_turn", None) if sc is not None else None
        for part in getattr(model_turn, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline is not None else None
            if data and not self.listen_only:
                if self.playback_phase in {"interrupted", "stopped", "cleanup"}:
                    continue
                if self._barge_state == "probable":
                    self._barge_state = "idle"
                    print(
                        f"[utterance] id={self._active_utterance_id} "
                        f"barge=cancelled generation_continued",
                        flush=True,
                    )
                if self.playback_phase in {"ready", "synthesizing", "finished"}:
                    self._set_playback("buffering", self._active_utterance_id)
                    self._set_playback("playing", self._active_utterance_id)
                self.speaking = True
                self._gen_done_at = 0.0
                self._note_first_audio()
                self._turn_audio_s = getattr(self, "_turn_audio_s", 0.0) + len(data) / (
                    2.0 * OUTPUT_SAMPLE_RATE
                )
                self.bridge.push_outbound_pcm(
                    data, sample_rate=OUTPUT_SAMPLE_RATE
                )

        # `msg.data` is the SDK's flattened view of the same audio; only use it
        # when parts gave us nothing, or the audio plays twice.
        if (
            not self.listen_only
            and not (getattr(model_turn, "parts", None) or [])
            and getattr(msg, "data", None)
            and self.playback_phase not in {"interrupted", "stopped", "cleanup"}
        ):
            if self._barge_state == "probable":
                self._barge_state = "idle"
            if self.playback_phase in {"ready", "synthesizing", "finished"}:
                self._set_playback("buffering", self._active_utterance_id)
                self._set_playback("playing", self._active_utterance_id)
            self.speaking = True
            self.bridge.push_outbound_pcm(msg.data, sample_rate=OUTPUT_SAMPLE_RATE)

        if sc is not None:
            out = getattr(sc, "output_transcription", None)
            if out is not None and getattr(out, "text", ""):
                self.__dict__.setdefault("_turn_said", []).append(out.text)
                self._emit(LiveEvent(kind="said", text=out.text))
            heard = getattr(sc, "input_transcription", None)
            if heard is not None and getattr(heard, "text", ""):
                text = heard.text
                if self.director_only:
                    pass
                elif not _is_real_speech(text):
                    print(f"[live] ignoring noise transcript: {text!r}", flush=True)
                elif self._is_self_echo(text):
                    print(f"[live] ignoring echo: {text!r}", flush=True)
                else:
                    if self._barge_state == "probable":
                        self._confirm_barge_in(reason="heard")
                    self.heard_count = getattr(self, "heard_count", 0) + 1
                    self._last_heard_at = time.monotonic()
                    self._await_answer_from = getattr(self, "_last_voice_at", 0.0) or self._last_heard_at
                    print(
                        f"[live] heard: {text!r} [voice-in] pcm bytes="
                        f"{getattr(self.bridge, 'pcm_recv_bytes', 0)} heard={self.heard_count}",
                        flush=True,
                    )
                    try:
                        self._heard.put_nowait(text)
                    except queue.Full:
                        pass
                    self._emit(LiveEvent(kind="heard", text=text))
            if getattr(sc, "turn_complete", False):
                self.speaking = False
                now_tc = time.monotonic()
                self._gen_done_at = now_tc
                self._drain_until = now_tc + min(
                    MAX_DRAIN_S, max(0.0, getattr(self, "_turn_audio_s", 0.0))
                ) + 0.3
                self._turn_audio_s = 0.0
                if self._barge_state == "probable":
                    self._barge_state = "idle"
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
    if cmd.mode == "acknowledge":
        return (
            f"[Say a short natural acknowledgement{lang_hint} that you understood "
            f"and are working on it. Use the following as the intent, not word-for-word: "
            f"{cmd.text}. One sentence only, then stop.]"
        )
    if cmd.mode == "result":
        return (
            f"[Deliver the following result to the person{lang_hint}, conversationally, "
            f"then stop. Do not add follow-up questions. Do not call any tools.] {cmd.text}"
        )
    if cmd.mode == "error":
        return (
            f"[Say the following recovery message{lang_hint}, naturally, then stop. "
            f"Do not reveal technical details.] {cmd.text}"
        )
    if cmd.mode == "natural":
        return (
            f"[Say the following to the person{lang_hint}, naturally and conversationally, "
            f"then stop. Do not add extra commentary or questions after. Do not call any tools.] {cmd.text}"
        )
    # verbatim — used only for intake questions where rephrasing would change meaning
    return (
        f"[Say the following to the person now{lang_hint}, exactly as written, then stop. Do not call any tools.] "
        f"{cmd.text}"
    )
