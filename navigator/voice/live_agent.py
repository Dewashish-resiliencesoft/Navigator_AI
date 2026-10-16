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


def is_echo_of_heard(text: str, heard: list) -> bool:
    """True when a line to speak is (mostly) something the prospect just said."""
    import re

    norm = lambda t: re.findall(r"\w+", (t or "").lower())  # noqa: E731
    w = norm(text)
    if len(w) < 3:
        return False
    for h in heard or []:
        hw = norm(h)
        if len(hw) < 3:
            continue
        common = sum(1 for x in w if x in set(hw))
        if common / len(w) >= 0.8 and common / len(hw) >= 0.6:
            return True
    return False


def _sentence_tail(text: str, said: str) -> str:
    """Unspoken remainder, backed up to the start of its sentence."""
    import re

    words = (text or "").split()
    norm = lambda w: re.sub(r"[\W_]", "", w.lower())  # noqa: E731
    cut = 0
    for sw in (norm(w) for w in (said or "").split()):
        for k in range(cut, min(cut + 6, len(words))):
            if sw and norm(words[k]) == sw:
                cut = k + 1
                break
    if cut >= len(words):
        return ""
    while cut > 0 and not words[cut - 1].endswith((".", "!", "?")):
        cut -= 1
    return " ".join(words[cut:])


def _unspoken_tail(line: str, said: str) -> str:
    """Words of ``line`` after the last one the model said IN ORDER, when it
    covered < 40% of the line. Matching in order (not as a bag of words) is what
    stops a common word said early ("the", "tab") from making the tail start near
    the end and dropping most of the line (14:54 run: 'or add—' -> 'Manual Setup tab.')."""
    import re

    words = (line or "").split()
    if len(words) < 7:
        return ""
    norm = lambda w: re.sub(r"[\W_]", "", w.lower())  # noqa: E731
    said_words = [norm(w) for w in (said or "").split()]
    said_words = [w for w in said_words if w]
    if not said_words:
        return ""  # no transcript at all: cannot judge, don't double-speak
    pos = 0
    for sw in said_words:
        for k in range(pos, min(pos + 6, len(words))):
            if norm(words[k]) == sw:
                pos = k + 1
                break
    if pos / len(words) >= 0.4:
        return ""
    return " ".join(words[pos:])


_ACTION_CLAIM = None


def claims_screen_action(text: str) -> bool:
    """Live said it did/does something on screen ('I'm filling the form',
    'let me show you the form', 'I'm already on the Phonebook page')."""
    import re

    global _ACTION_CLAIM
    if _ACTION_CLAIM is None:
        _ACTION_CLAIM = re.compile(
            r"\b(?:i'?m|i am|i'?ve|i have|let me|i'?ll|i will|we'?re|we are)\s+(?:now\s+|just\s+)?"
            r"(?:fill\w*|open\w*|click\w*|show\w*|creat\w*|typ\w*|enter\w*|navigat\w*|go(?:ing)? to|"
            r"tak\w+ you|already on|on the \w+ (?:page|screen)|submit\w*|sav\w*)\b"
            r"|\bshall i (?:save|submit|create|press)\b", re.I)
    return bool(_ACTION_CLAIM.search((text or "").replace("\u2019", "'")))


def _is_real_speech(text: str) -> bool:
    """Real words, not STT noise like 'e' / 'uh' / '.'."""
    alnum = "".join(ch for ch in (text or "") if ch.isalnum())
    if len(alnum) < 3:
        return False
    return alnum.lower() not in {"uhh", "umm", "hmm", "mmm", "ahh", "ehh"}



import weakref

#: Every LiveAgent that has started and not closed: there must only ever be one.
_OPEN_AGENTS: "weakref.WeakSet[LiveAgent]" = weakref.WeakSet()


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
    #: Other Gemini keys to switch to when this one hits its quota MID-call
    #: (12:00 audit: key quota ran out at step 17 and the rest was silent).
    fallback_keys: list[str] = field(default_factory=list)


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
        for other in list(_OPEN_AGENTS):
            if other is not self and not other._stop.is_set():
                print("[audio-arbiter] concurrent writer: another Live session is still open — "
                      "closing it before starting this one", flush=True)
                try:
                    other.close()
                except Exception:  # noqa: BLE001
                    pass
        _OPEN_AGENTS.add(self)
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
        # Demo over / human left: nothing more may reach the meeting.
        self.listen_only = True
        try:
            dropped = self.bridge.clear_outbound()
            if dropped:
                print(f"[audio-arbiter] demo ended — dropped {dropped} queued chunk(s)", flush=True)
        except Exception:  # noqa: BLE001
            pass
        _OPEN_AGENTS.discard(self)
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

    VAD_RMS = 1200.0
    VAD_MIN_S = 0.35

    def _vad_feed(self, rms: float, now: float) -> None:
        """Prospect's own track (no bot echo): ~0.35s of sustained voice during a
        scripted line stops playback at once — no waiting ~2-4s for the
        transcript. The transcript then confirms; silence -> the line resumes."""
        d = self.__dict__
        if rms > self.VAD_RMS:
            if not d.get("_vad_run_start"):
                d["_vad_run_start"] = now
            d["_vad_last"] = now
        elif now - d.get("_vad_last", 0.0) > 0.25:
            d["_vad_run_start"] = 0.0
            return
        start = d.get("_vad_run_start") or 0.0
        playing = (
            (getattr(self, "_in_say", False) or self.speaking)
            and self.playback_phase not in {"interrupted", "stopped", "cleanup", "ready"}
        )
        anchor = max(
            float(getattr(self, "_say_queued_at", 0.0) or 0.0),
            float(getattr(self, "_audio_rx_at", 0.0) or 0.0),
        )
        # Scripted lines and Q&A answers both stop. The old gate required
        # ``_in_say``, so a prospect talking over a Live answer waited for a
        # transcript that never confirmed the interrupt.
        if (start and now - start >= self.VAD_MIN_S and playing and not self.interrupted
                and d.get("_vad_cut_at", 0.0) < anchor):
            d["_vad_cut_at"] = now
            print(f"[barge] voice energy {rms:.0f} for {now - start:.2f}s — stopping playback now", flush=True)
            self._confirm_barge_in(reason="vad")

    def _vad_false_alarm(self, started: float, wait_s: float = 3.0) -> bool:
        """After a VAD stop: True if no real question arrived (noise/cough)."""
        cut = float(self.__dict__.get("_vad_cut_at", 0.0) or 0.0)
        if cut < started:
            return False
        # The question's transcript lands only after they stop talking (5s in the
        # 16:37 audit), so wait for quiet + wait_s (12s cap).
        hard = cut + 12.0
        while not self._stop.is_set():
            now = time.monotonic()
            if getattr(self, "_last_heard_at", 0.0) >= cut - 0.5:
                return False
            quiet_since = max(cut, float(self.__dict__.get("_vad_last", 0.0) or 0.0))
            if now >= hard or now - quiet_since >= wait_s:
                break
            self._pumped_wait(self._stop, 0.1)
        return getattr(self, "_last_heard_at", 0.0) < cut - 0.5

    _OPEN_RE = None

    def _unsolicited_audio(self, now: float | None = None) -> bool:
        """Model talking on its own between scripted lines, with no question
        pending (17:39 run: 'This is the Dashboard…' spoken on the login screen
        after a refused tool call; it also paused the flow 12s)."""
        now = time.monotonic() if now is None else now
        d = self.__dict__
        ended = float(d.get("_say_ended_at", 0.0) or 0.0)
        if getattr(self, "_in_say", False) or not ended:
            return False
        if now - float(d.get("_nudge_sent_at", 0.0) or 0.0) < 6.0:
            return False
        heard = float(getattr(self, "_last_heard_at", 0.0) or 0.0)
        if heard and now - heard < 30.0:  # answering a question (also one asked mid-line)
            return False
        if now - float(d.get("_unsolicited_log_at", 0.0) or 0.0) > 3.0:
            d["_unsolicited_log_at"] = now
            print("[live] dropping unsolicited model audio (no question pending)", flush=True)
        return True

    def _backstop_open(self, text: str, wait_s: float = 2.5) -> None:
        """'Can you open Local Seats?' -> if Live makes no tool call within
        wait_s (16:50 audit: it made none), open the matched known screen
        ourselves, with a spoken ack and an honest failure line."""
        import re
        import threading

        if LiveAgent._OPEN_RE is None:
            LiveAgent._OPEN_RE = re.compile(
                r"\b(?:open|go to|take me to|navigate to|switch to)\s+(?:the\s+|our\s+|my\s+)?"
                r"([\w][\w &'-]{1,40}?)(?:\s+(?:page|screen|section|tab|for me|please))*\s*[?.!]*$", re.I)
        m = LiveAgent._OPEN_RE.search((text or "").strip())
        handler = getattr(self.cfg, "tool_handler", None)
        if not m or handler is None:
            return
        try:
            from navigator.meeting.ui_jobs import resolve_screen

            label, path = resolve_screen(m.group(1), strict=True)
        except Exception:  # noqa: BLE001
            return
        if not path:
            return
        t0 = time.monotonic()

        def run() -> None:
            end = t0 + wait_s
            while time.monotonic() < end and not self._stop.is_set():
                if getattr(self, "_last_tool_at", 0.0) >= t0:
                    return  # Live handled it
                time.sleep(0.1)
            if getattr(self, "_last_tool_at", 0.0) >= t0 or self._stop.is_set():
                return
            print(f"[live] no tool call for {text!r} — opening {label!r} ({path}) directly", flush=True)
            self._last_tool_at = time.monotonic()
            self._cmds.put(_Cmd(kind="nudge", text=f"Sure, opening {label} now."))
            try:
                res = handler("show_feature", {"target": label})
            except Exception as exc:  # noqa: BLE001
                res = {"ok": False, "error": str(exc)[:120]}
            if not (isinstance(res, dict) and res.get("ok")):
                self._cmds.put(_Cmd(kind="nudge", text=f"Sorry, {label} did not open just now."))
            else:
                self._cmds.put(_Cmd(kind="context", text=f"The screen now shows {label} ({path})."))

        threading.Thread(target=run, daemon=True, name="open-backstop").start()

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
        self._drop_inflight_audio = True
        # A line that never produced PCM must not stamp the next answer.
        self._narration_chunk_open = False
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
        from navigator.agent.speech_safety import humanize_ids

        text = humanize_ids(text)
        if not (text or "").strip():
            return
        uid = utterance_id or ""
        if is_echo_of_heard(text, getattr(self, "_heard_texts", [])):
            # 13:23 run: a degraded planner returned the prospect's own words
            # ("Do it a screen. I am not able to see your screen.") as the reply.
            print(f"[utterance] id={uid} refusing to speak the prospect's own words", flush=True)
            return
        self._say_started_at = time.monotonic()
        called_at = self._say_started_at
        key = " ".join(text.lower().split())
        spoken = self.__dict__.setdefault("_spoken_keys", set())
        if mode == "verbatim" and len(key) > 20 and key in spoken:
            print(f"[utterance] id={uid} skip replay of already-spoken line", flush=True)
            return
        spoken.add(key)
        _lang0 = str(getattr(self.cfg, "language", "en") or "en")
        from navigator.voice.line_prep import lead_and_rest as _lead_and_rest

        _raw_parts = _lead_and_rest(text)
        if _lang0 != "en" and mode in ("verbatim", "natural"):
            from navigator.voice import line_prep

            # Translate sentence by sentence while waiting for the floor.
            # The first sentence is ready when we start; the rest finish
            # while that sentence is already being spoken.
            for _piece in _raw_parts:
                line_prep.prefetch(_piece, _lang0)
        if not self.can_start_utterance():
            print(
                f"[utterance] id={uid} wait_idle phase={self.playback_phase} "
                f"queue={self._cmds.qsize()}",
                flush=True,
            )
            self._wait_idle_or_recover(uid)
        if self._barge_state == "confirmed":
            self._barge_state = "idle"
        if getattr(self, "_last_tool_at", 0.0) > called_at:
            # A prospect's request moved the screen while this scripted line waited
            # for the floor (15:09 audit: the WordPress line played over Phonebook).
            # Its words no longer match the screen: drop it, never speak it there.
            print(f"[utterance] id={uid} scripted line dropped: screen changed by Q&A before it could play", flush=True)
            return
        # Previous audio (e.g. a Q&A answer, generated faster than realtime) is
        # still playing in the meeting: never start the next line over it.
        try:
            pe = float(getattr(self.bridge, "out_play_end", 0.0) or 0.0)
        except (TypeError, ValueError):
            pe = 0.0
        if pe - time.monotonic() > 0.05:
            print(f"[utterance] id={uid} waiting {pe - time.monotonic():.1f}s for earlier audio to play out", flush=True)
            end = min(pe, time.monotonic() + 20.0)
            while time.monotonic() < end and not self._stop.is_set():
                self._pump_ui()
                self._stop.wait(min(0.1, max(0.0, end - time.monotonic())))
        lang = _lang0
        from navigator.voice.line_prep import est_seconds, translate as _translate

        self._rotate_before_line(uid)

        # Always lead with the first sentence. Waiting to translate or
        # synthesize the whole paragraph is what made the first audio late.
        parts = _raw_parts or [text]
        self.last_spoken = text
        self.interrupted = False
        self.speaking = True
        self._set_playback("synthesizing", uid or None)
        started = time.monotonic()
        sent_at_start = self._audio_s_sent()
        self._in_say = True
        self._say_queued_at = time.monotonic()
        print(f"[speak] {text}", flush=True)
        for i, raw_part in enumerate(parts):
            part = raw_part
            if lang != "en" and mode in ("verbatim", "natural"):
                # Only this sentence blocks. Later ones were prefetched above.
                t0 = time.monotonic()
                part = _translate(raw_part, lang)
                if part != raw_part:
                    print(
                        f"[speak] pre-translated piece {i + 1}/{len(parts)} "
                        f"to {lang} in {time.monotonic() - t0:.1f}s",
                        flush=True,
                    )
            self._turn_done.clear()
            self._turn_said = []
            self._last_turn_audio_s = 0.0
            # In-flight piece: re-sent on session reconnect (16:20 1006 drop lost it).
            self._inflight = (part, mode, uid)
            self._inflight_audio_at_send = self._audio_s_sent()
            expect = getattr(self.bridge, "expect_line", None)
            if callable(expect):
                expect(est_seconds(part, lang))
            if getattr(self, "_mute_reply", False):
                self._skip_next_end = True  # the muted reply's own end is still coming
            self._mute_reply = False
            self._cmds.put(_Cmd(kind="say", text=part, mode=mode))
            if len(parts) > 1:
                print(f"[speak] piece {i + 1}/{len(parts)}: {part[:60]}", flush=True)
            self._wait_turn_with_watchdog(part, mode, uid, 90)
            muted_mid = self.__dict__.get("_muted_mid_at", 0.0) >= started and not self.interrupted
            if muted_mid or (self.__dict__.get("_vad_cut_at", 0.0) >= started and self._vad_false_alarm(started)):
                tail = _sentence_tail(part, " ".join(self._turn_said))
                print(f"[{'speak' if muted_mid else 'barge'}] "
                      f"{'muted refused-tool reply' if muted_mid else 'no question followed'}"
                      f" — resuming: {tail[:60]!r}", flush=True)
                self.__dict__["_muted_mid_at"] = 0.0
                self.__dict__["_vad_cut_at"] = 0.0
                self.interrupted = False
                self._barge_state = "idle"
                self.speaking = True
                if tail:
                    self._turn_said = []
                    self._turn_done.clear()
                    self._inflight = (tail, "verbatim", uid)
                    self._cmds.put(_Cmd(kind="say", text=tail, mode="verbatim"))
                    self._wait_turn_with_watchdog(tail, "verbatim", uid, 60)
                    started = time.monotonic()  # the voice before this is handled
                    continue
            rest = _unspoken_tail(part, " ".join(self._turn_said))
            cut_by_user = max(getattr(self, "_last_heard_at", 0.0),
                              getattr(self, "_last_voice_at", 0.0)) >= started
            asked = getattr(self, "_last_heard_at", 0.0) >= started
            if (rest and cut_by_user) or (asked and i + 1 < len(parts)):
                # The prospect spoke during this line: Live answers them; sending
                # the next piece would talk over (and cancel) that answer.
                print(f"[speak] line cut by prospect question; not replaying tail"
                      f"{' / dropping remaining pieces' if i + 1 < len(parts) else ''}", flush=True)
                break
            if self.interrupted or self._stop.is_set():
                break
            if getattr(self, "_last_tool_at", 0.0) > called_at and i + 1 < len(parts):
                print("[speak] screen moved by a prospect request mid-line — dropping the remaining pieces", flush=True)
                break
            produced = float(getattr(self, "_last_turn_audio_s", 0.0) or 0.0)
            if rest and produced >= 0.6 * est_seconds(part, lang):
                # Audio for (nearly) the whole piece was generated; the transcript
                # just lagged. Replaying the "tail" spoke words twice.
                rest = ""
            for _ in range(3):
                if not rest:
                    break
                # The turn "ended" early but the model often keeps going after a
                # ~1s gap (13:22 "go back—" ... "to the"): replaying then spoke
                # fragments twice. Wait for real quiet; if audio resumes, wait
                # for that turn's end and re-check.
                rx0 = float(getattr(self, "_audio_rx_at", 0.0) or 0.0)
                q0 = time.monotonic()
                resumed = False
                while time.monotonic() - q0 < 2.5 and not self._stop.is_set():
                    self._pumped_wait(self._stop, 0.1)
                    if float(getattr(self, "_audio_rx_at", 0.0) or 0.0) > rx0 + 1e-3:
                        resumed = True
                        break
                if resumed:
                    self._turn_done.clear()
                    self._wait_turn_with_watchdog(part, mode, uid, 60)
                rest = _unspoken_tail(part, " ".join(self._turn_said))
                if not resumed:
                    break
            if rest and float(getattr(self, "_last_turn_audio_s", 0.0) or 0.0) >= 0.6 * est_seconds(part, lang):
                rest = ""
            if rest and len(rest.split()) >= 3 and self._barge_state != "probable":
                # Model stopped early (last run: spoke only "let go" of a long line).
                print(f"[speak] line cut short; finishing: {rest}", flush=True)
                self._turn_said = []
                self._turn_done.clear()
                self._inflight = (rest, "verbatim", uid)
                self._cmds.put(_Cmd(kind="say", text=rest, mode="verbatim"))
                self._pumped_wait(self._turn_done, 60)
            elif rest:
                print(f"[speak] short tail not replayed: {rest!r}", flush=True)
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
        self._say_ended_at = time.monotonic()
        self._skip_next_end = False  # never carry over into a Q&A answer turn
        self._inflight = None

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
                # Single-voice rule: never start a line over audio that is still
                # playing or a Live answer still being generated.
                live_answer = self.speaking and not getattr(self, "_in_say", False)
                if (now < play_end or (live_answer and now - self._last_audio_rx_at() < 2.0)) \
                        and now < deadline + 45.0:
                    if not getattr(self, "_floor_wait_logged", False):
                        self._floor_wait_logged = True
                        print(f"[audio-arbiter] id={uid} floor busy (audio playing / Live answering) — "
                              f"holding scripted line", flush=True)
                    self._pump_ui()
                    self._playback_idle.wait(timeout=0.25)
                    continue
                self._floor_wait_logged = False
                print(f"[utterance] id={uid} wait_idle timeout {max_wait_s}s -> force ready", flush=True)
                # Usually a live answer still talking: not a glitch to apologise for.
                self.last_wait_timeout_at = now
                break
            self._pump_ui()
            self._playback_idle.wait(timeout=0.25)
        self._set_playback("ready", uid or None)

    def _last_audio_rx_at(self) -> float:
        return float(getattr(self, "_audio_rx_at", 0.0) or 0.0)

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
        try:
            # the meeting's own play-out clock (includes audio queued before this line)
            pe = float(getattr(self.bridge, "out_play_end", 0.0) or 0.0) - time.monotonic()
        except (TypeError, ValueError):
            pe = 0.0
        if 0 < pe < MAX_DRAIN_S + 5:
            slack = max(slack, pe)
        if slack > 0 and self.cfg.ui_pump is None:
            self._stop.wait(min(slack, MAX_DRAIN_S))
        elif slack > 0:
            end = time.monotonic() + min(slack, MAX_DRAIN_S)
            while time.monotonic() < end and not self._stop.is_set():
                self._pump_ui()
                self._stop.wait(min(0.1, max(0.0, end - time.monotonic())))

    def _arm_narration_chunk(self, text: str, *, provider: str) -> None:
        """Remember the sentence whose first PCM should share the sync clock."""
        self._narration_chunk_text = text
        self._narration_provider = provider
        self._narration_chunk_open = True

    def _emit_narration_chunk(self) -> None:
        """One monotonic stamp per sentence, at the first audible buffer."""
        if not getattr(self, "_narration_chunk_open", False):
            return
        self._narration_chunk_open = False
        text = str(getattr(self, "_narration_chunk_text", "") or "")
        if not text:
            return
        provider = str(getattr(self, "_narration_provider", "gemini_live") or "gemini_live")
        try:
            from navigator.agent_runtime.sync_clock import emit_narration_chunk

            emit_narration_chunk(text, provider)
        except Exception as exc:  # noqa: BLE001
            print(f"[voice] narration clock failed: {exc}", flush=True)

    def _note_first_audio(self) -> None:
        from navigator.core import logtime

        self._emit_narration_chunk()
        now = time.monotonic()
        jit = 250.0  # Attendee jitter buffer (patch) — audio heard ~this much later
        q = getattr(self, "_say_queued_at", 0.0)
        if q and getattr(self, "_in_say", False):
            ms = (now - q) * 1000
            self._say_queued_at = 0.0
            logtime.record("text_queued->first_audio", ms)
            logtime.record("first_audio->heard_in_zoom_est", jit + 40)
            print(f"[voice] ttfa_ms={ms:.0f} path=scripted", flush=True)
            return
        u = getattr(self, "_await_answer_from", 0.0)
        if u and not getattr(self, "_in_say", False):
            ms = (now - u) * 1000
            self._await_answer_from = 0.0
            logtime.record("user_end->first_audio", ms)
            logtime.record("first_audio->heard_in_zoom_est", jit + 40)
            print(f"[voice] ttfa_ms={ms:.0f} path=answer", flush=True)

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
            if now - float(getattr(self, "_audio_rx_at", 0.0) or 0.0) > 8.0:
                # turn end lost (15:34 audit hung 14 min on this): audio stopped long ago
                self.speaking = False
            else:
                self._answer_seen_at = now
                return True
        # Resume only after the answer finished + 1.5s of silence.
        if now - getattr(self, "_answer_seen_at", 0.0) < 1.5:
            return True
        play_end = max(float(getattr(self.bridge, "_tap_play_end", 0.0) or 0.0),
                       float(getattr(self.bridge, "out_play_end", 0.0) or 0.0))
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
            # Only a real transcribed request opens the user's turn. Mic energy
            # (_last_voice_at) let Live run do_task 'login' on its own mid-line
            # at 18:53:34, moving the screen under the narration.
            heard = getattr(self, "_last_heard_at", 0.0)
            user_turn = time.monotonic() - heard < 10.0 and (
                not getattr(self, "_in_say", False) or heard >= getattr(self, "_say_queued_at", 0.0))
            if not user_turn and heard and getattr(self, "_tool_for_heard", None) != heard \
                    and time.monotonic() - heard < 25.0 and not getattr(self, "_in_say", False):
                # The question's own tool call, late because Live answered aloud
                # first (12s at 11:49 audit: "show me how to add a contact" was refused).
                user_turn = True
            if user_turn:
                self._tool_for_heard = heard
            if not user_turn:
                # Scripted line in progress: the flow executor owns the screen.
                # A model-initiated click here raced the flow and cut the line.
                mid_line = bool(getattr(self, "_in_say", False)) and not self._turn_done.is_set()
                print(f"[live] tool_call {name} refused: not the user's turn (spontaneous)"
                      f"{'' if mid_line else ' — line already done, reply muted'}", flush=True)
                # 11:18 / 16:21:15 runs: the reply after a refusal repeated the
                # line's last words. Always mute it; mid-line, its end still ends
                # the piece and say() replays the real unspoken tail verbatim.
                self._mute_reply = True
                self._mute_keeps_end = mid_line
                if mid_line:
                    self._muted_mid_at = time.monotonic()
                responses.append(types.FunctionResponse(
                    id=getattr(fc, "id", None), name=name,
                    response={"ok": False, "error": (
                        "Not now. The demo drives the screen itself. Do not call tools "
                        "and do not say anything.") if mid_line else (
                        "Not now. The line is finished. Do not call tools and do not say anything.")}))
                continue
            try:
                self._last_tool_at = time.monotonic()
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
        if getattr(self.bridge, "echo_free", False):
            # Per-participant audio never contains our own voice: a match is the
            # prospect repeating words from the line (17:33 "inboxes" was dropped).
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
                    old = getattr(self, "_session", None)
                    self._session = session
                    self._session_gen = getattr(self, "_session_gen", 0) + 1
                    self._writer_tag = f"live{id(self) & 0xffff:x}-s{self._session_gen}"
                    if old is not None and old is not session:
                        print(f"[audio-arbiter] new Live session {self._writer_tag}; "
                              f"previous session's audio is now ignored", flush=True)
                    self._ready.set()
                    if self._resumption_handle:
                        print("[live] Gemini Live session resumed", flush=True)
                    self._reconnect_req = False
                    # a mute armed in the dead session would swallow the re-sent line
                    self._mute_reply = False
                    self._mute_keeps_end = False
                    if getattr(self, "_sessions_opened", 0):
                        self._resend_inflight()
                    self._sessions_opened = getattr(self, "_sessions_opened", 0) + 1
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
                if "quota" in str(exc).lower() and self.cfg.fallback_keys:
                    self.cfg.api_key = self.cfg.fallback_keys.pop(0)
                    client = genai.Client(api_key=self.cfg.api_key)
                    self._resumption_handle = None  # handles are per key/project
                    attempt = 0
                    print(f"[live] key quota exhausted — switching to next Gemini key "
                          f"({len(self.cfg.fallback_keys)} left)", flush=True)
                    await asyncio.sleep(0.3)
                    continue
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

    def _check_unbacked_claim(self) -> None:
        """An answer that claims a screen action with no tool call since the
        prospect spoke changed NOTHING on screen (14:54: 'I'm filling the form
        for you... name Akash' with no do_task). Tell the model, so it calls the
        tool for real or corrects itself, and never invents details."""
        said = " ".join(getattr(self, "_answer_said", []) or [])
        self._answer_said = []
        if not said or getattr(self, "_in_say", False):
            return
        heard = getattr(self, "_last_heard_at", 0.0)
        if not heard or getattr(self, "_last_tool_at", 0.0) >= heard or not claims_screen_action(said):
            return
        if getattr(self, "_claim_fix_for", None) == heard:
            return  # one correction per prospect request
        self._claim_fix_for = heard
        screen = getattr(self, "screen_hint", "") or "unknown"
        print(f"[live] unbacked screen claim (no tool call): {said[:90]!r} — correcting", flush=True)
        self._cmds.put(_Cmd(kind="context", text=(
            "You just told the prospect you did something on the screen, but you called NO tool, so "
            f"nothing changed. The screen right now is: {screen}. Do it for real now with do_task "
            "(or show_feature / click_control) and then describe only what the tool result shows; or "
            "briefly correct yourself. Never invent details such as names or phone numbers: ask the "
            "prospect for them. Do not push to move on.")))

    def _rotate_before_line(self, uid: str) -> None:
        """Server sent GoAway: resume on a fresh session BETWEEN lines. Waiting
        until it aborts the socket (1008) cost 5.8s of silence mid-line at 13:22."""
        g = getattr(self, "_goaway_gen", None)
        if g is None or g != getattr(self, "_session_gen", 0) or self._stop.is_set():
            return
        self._goaway_gen = None
        print(f"[utterance] id={uid} GoAway pending — reconnecting before the line", flush=True)
        self.request_reconnect()
        end = time.monotonic() + 6.0
        while getattr(self, "_session_gen", 0) == g and time.monotonic() < end and not self._stop.is_set():
            self._stop.wait(0.05)

    def request_reconnect(self) -> None:
        self._reconnect_req = True

    def _wait_turn_with_watchdog(self, text: str, mode: Any, uid: str, timeout: float) -> None:
        """Wait for the model turn; recover when Live goes silent.

        - No audio 10s after queueing -> reconnect (reconnect re-sends the line).
        - Audio started but stopped >8s mid-line without turn_complete -> same.
        - Still silent 25s after the last progress -> give up on the line.
        16:20 run: ``self.speaking`` (set before the wait) counted as "got audio",
        so after a 1006 drop the watchdog never fired and the demo sat silent 65s."""
        start = time.monotonic()
        last_audio = self._audio_s_sent()
        last_progress = start
        recoveries = 0
        while True:
            if self._pumped_wait(self._turn_done, 0.5):
                return
            if self._stop.is_set():
                return
            now = time.monotonic()
            cur = self._audio_s_sent()
            if cur > last_audio + 1e-3:
                last_audio, last_progress = cur, now
            # Hosted TTS blocks in another thread before the first PCM byte.
            # Don't treat that wait as a dead Gemini session and reconnect.
            if (
                getattr(self, "_nvidia_synth", False) or getattr(self, "_external_synth", False)
            ) and now - start < 25.0:
                last_progress = now
            quiet = now - last_progress
            started_audio = cur > self._inflight_audio0() + 1e-3
            limit = 8.0 if started_audio else 10.0
            said_n = len(" ".join(getattr(self, "_turn_said", []) or []).split())
            if started_audio and quiet >= 2.5 and said_n >= 0.8 * max(1, len(text.split())):
                # Line was spoken but turn_complete got lost (seen after a resumed session).
                print(f"[speak] id={uid} line spoken ({said_n} words), no turn_complete — done", flush=True)
                mark = getattr(self.bridge, "mark_line_end", None)
                if callable(mark):
                    mark()  # next utterance gets a fresh prebuffer; its start gap isn't an underrun
                self._turn_done.set()
                return
            if now - float(self.__dict__.get("_dropped_rx_at", 0.0) or 0.0) < 1.5:
                last_progress = now  # muted/unsolicited audio still streaming
                continue
            if quiet >= limit and recoveries < 2 and not self.answering:
                recoveries += 1
                print(f"[speak] id={uid} Live silent {quiet:.0f}s "
                      f"({'mid-line' if started_audio else 'no audio'}) — reconnecting, line will be re-sent",
                      flush=True)
                self.request_reconnect()
                last_progress = now
            elif quiet >= 25 and recoveries >= 2:
                print(f"[speak] id={uid} Live still silent — skipping line", flush=True)
                self._turn_done.set()
                return
            elif now - start >= timeout:
                print(f"[speak] id={uid} turn timeout {timeout:.0f}s — continuing", flush=True)
                return

    def _inflight_audio0(self) -> float:
        return float(getattr(self, "_inflight_audio_at_send", 0.0) or 0.0)

    def _resend_inflight(self) -> None:
        """After a reconnect: re-send the scripted line the dead session lost."""
        inflight = getattr(self, "_inflight", None)
        if not inflight or self._turn_done.is_set() or self._stop.is_set():
            return
        text, mode, uid = inflight
        said_n = len(" ".join(getattr(self, "_turn_said", []) or []).split())
        words = text.split()
        # Resume from the start of the sentence that was cut, so it reads naturally.
        cut = min(said_n, len(words))
        while cut > 0 and not words[cut - 1].endswith((".", "!", "?")):
            cut -= 1
        tail = " ".join(words[cut:])
        if not tail:
            return
        print(f"[speak] id={uid} re-sending in-flight line after reconnect: {tail[:80]!r}", flush=True)
        self._cmds.put(_Cmd(kind="say", text=tail, mode=mode))

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
                rms = (sum(x * x for x in a[::8]) / max(1, len(a[::8]))) ** 0.5 if len(a) else 0.0
                if rms > 900:
                    self._last_voice_at = time.monotonic()
                self._vad_feed(rms, time.monotonic())
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
                    if session is not self._session:
                        self.stale_session_msgs = getattr(self, "stale_session_msgs", 0) + 1
                        print("[audio-arbiter] concurrent writer: message from a replaced Live "
                              "session dropped", flush=True)
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
            self._goaway_gen = getattr(self, "_session_gen", 0)

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
            qa = getattr(self, "_say_queued_at", 0.0)
            if now - qa < 1.2 and getattr(self, "_last_voice_at", 0.0) < qa:
                # Sending the next scripted line makes the server mark the previous
                # (finished) generation "interrupted" ~100ms later. Not the user.
                return
            if getattr(self, "_in_say", False) and getattr(self, "_last_heard_at", 0.0) < getattr(self, "_say_started_at", 0.0):
                # Our own next-piece/tail send or echo during this line: only a real
                # transcript of the prospect confirms a barge-in (13:22 run).
                return
            if qa and getattr(self, "_last_voice_at", 0.0) < qa and getattr(self, "_in_say", False):
                # No prospect voice since this line was queued: the "interruption"
                # is our own next-piece send / echo, not a barge-in.
                return
            if self._barge_state != "probable":
                self._note_probable_barge()
            voice_at = float(getattr(self, "_last_voice_at", 0.0) or 0.0)
            # Inbound energy in the last 0.8s is the prospect, not our own
            # next-piece send. Stop playback now; do not wait for the transcript
            # (that path used to return and never call _confirm_barge_in).
            if (
                voice_at
                and now - voice_at < 0.8
                and (self.speaking or getattr(self, "_in_say", False))
            ):
                self._confirm_barge_in(reason="voice")
                return
            if now - self._barge_probable_at < self._barge_confirm_s:
                print(
                    f"[live] barge-in still probable "
                    f"({now - self._barge_probable_at:.2f}s < {self._barge_confirm_s}s)",
                    flush=True,
                )
                return
            # No recent voice energy: hold until a real transcript confirms.
            print("[live] barge-in held: waiting for real STT speech", flush=True)
            return

        # One ServerContent can carry several parts (audio plus transcript).
        model_turn = getattr(sc, "model_turn", None) if sc is not None else None
        for part in getattr(model_turn, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline is not None else None
            if data and not self.listen_only:
                if getattr(self, "_drop_inflight_audio", False) or self.playback_phase in {
                    "interrupted", "stopped", "cleanup"
                }:
                    continue
                if getattr(self, "_mute_reply", False) or self._unsolicited_audio():
                    # not voiced, but the model IS generating: not a stall
                    self._dropped_rx_at = time.monotonic()
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
                self._turn_closed = False
                self._gen_done_at = 0.0
                self._note_first_audio()
                self._turn_audio_s = getattr(self, "_turn_audio_s", 0.0) + len(data) / (
                    2.0 * OUTPUT_SAMPLE_RATE
                )
                self._audio_rx_at = time.monotonic()
                self.bridge.push_outbound_pcm(
                    data, sample_rate=OUTPUT_SAMPLE_RATE, writer=getattr(self, "_writer_tag", "live")
                )

        # `msg.data` is the SDK's flattened view of the same audio; only use it
        # when parts gave us nothing, or the audio plays twice.
        if (
            not self.listen_only
            and not (getattr(model_turn, "parts", None) or [])
            and getattr(msg, "data", None)
            and not getattr(self, "_drop_inflight_audio", False)
            and self.playback_phase not in {"interrupted", "stopped", "cleanup"}
        ):
            if self._barge_state == "probable":
                self._barge_state = "idle"
            if self.playback_phase in {"ready", "synthesizing", "finished"}:
                self._set_playback("buffering", self._active_utterance_id)
                self._set_playback("playing", self._active_utterance_id)
            self.speaking = True
            self._turn_closed = False
            self._audio_rx_at = time.monotonic()
            self.bridge.push_outbound_pcm(msg.data, sample_rate=OUTPUT_SAMPLE_RATE,
                                          writer=getattr(self, "_writer_tag", "live"))

        if sc is not None:
            out = getattr(sc, "output_transcription", None)
            if out is not None and getattr(out, "text", "") and not getattr(self, "_mute_reply", False):
                self.__dict__.setdefault("_turn_said", []).append(out.text)
                if not getattr(self, "_in_say", False):
                    self.__dict__.setdefault("_answer_said", []).append(out.text)
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
                    self.__dict__.setdefault("_heard_texts", []).append(text)
                    try:
                        from navigator.meeting import task_runner as _tr

                        _tr.HEARD.append(text)
                        del _tr.HEARD[:-12]
                    except Exception:  # noqa: BLE001
                        pass
                    del self._heard_texts[:-6]
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
                    self._backstop_open(text)
            # gemini-3.1-flash-live sends generation_complete and often no
            # turn_complete at all: every line then sat 2.5s on the "no
            # turn_complete" watchdog before the next one could start.
            gc = bool(getattr(sc, "generation_complete", False))
            tcm = bool(getattr(sc, "turn_complete", False))
            if gc:
                self._uses_gc = True
            # Models that send generation_complete end turns on it only; their
            # turn_complete often lands late, after the NEXT piece's audio began,
            # which ended that piece at 0.5s ("line cut short; finishing" + the
            # tail spoken over the original = duplicated fragments, 11:18 run).
            ended = gc or (tcm and not getattr(self, "_uses_gc", False))
            if (gc or tcm) and getattr(self, "_mute_reply", False):
                self._mute_reply = False
                ended = ended and bool(getattr(self, "_mute_keeps_end", False))
                self._mute_keeps_end = False
            elif ended and getattr(self, "_skip_next_end", False) and getattr(self, "_in_say", False):
                self._skip_next_end = False
                ended = False
                print("[utterance] ignored the muted reply's turn end (line continues)", flush=True)
            # A turn ends once: generation_complete and a late turn_complete for the
            # SAME turn used to land after the next say() had started, ending that
            # line instantly — the flow then navigated while its audio played.
            done_now = ended and not getattr(self, "_turn_closed", False)
            if ended:
                self._turn_closed = True
            if done_now:
                self._drop_inflight_audio = False
                if getattr(self, "_narration_chunk_open", False):
                    self._narration_chunk_open = False
                self._last_turn_audio_s = getattr(self, "_turn_audio_s", 0.0)
                self.speaking = False
                mark = getattr(self.bridge, "mark_line_end", None)
                if callable(mark):
                    mark()
                now_tc = time.monotonic()
                self._gen_done_at = now_tc
                self._drain_until = now_tc + min(
                    MAX_DRAIN_S, max(0.0, getattr(self, "_turn_audio_s", 0.0))
                ) + 0.3
                self._turn_audio_s = 0.0
                if self._barge_state == "probable":
                    self._barge_state = "idle"
                self._turn_done.set()
                self._check_unbacked_claim()
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
            if cmd.kind in ("say", "nudge"):
                # Hosted TTS speaks the phrased line. Gemini is the fallback
                # and still hears the room. gemini_live skips the HTTP path.
                from navigator.core.settings import settings as voice_settings
                from navigator.voice.tts import try_hosted_tts

                provider = voice_settings.voice_provider
                self._arm_narration_chunk(cmd.text, provider="gemini_live")
                if provider in ("nvidia", "fish"):
                    self._narration_provider = provider
                    self._nvidia_synth = True
                    self._external_synth = True

                    def _cancel() -> bool:
                        return bool(self.interrupted or self._stop.is_set())

                    def _first_audio() -> None:
                        self._note_first_audio()

                    try:
                        audio_s = await asyncio.to_thread(
                            try_hosted_tts,
                            self.bridge,
                            cmd.text,
                            language=str(self.cfg.language or "en"),
                            cancel=_cancel,
                            on_first_audio=_first_audio,
                        )
                    finally:
                        self._nvidia_synth = False
                        self._external_synth = False
                    # 0.0 is a barge-in before the first byte, not a failure.
                    # Falling through to Gemini would speak the line again.
                    if audio_s is not None:
                        self._finish_nvidia_turn(cmd.text, audio_s)
                        continue
                    self._narration_provider = "gemini_live"
                    self._narration_chunk_open = True
            if cmd.kind == "nudge":
                self._nudge_sent_at = time.monotonic()
            try:
                await session.send_realtime_input(
                    text=_prompt_for(cmd, language=self.cfg.language)
                )
            except Exception as exc:  # noqa: BLE001
                self._emit(LiveEvent(kind="error", text=f"send text: {exc}"))
                self._turn_done.set()
                return

    def _finish_nvidia_turn(self, text: str, audio_s: float) -> None:
        """A phrased line was spoken by hosted TTS, not Gemini Live.

        Mark the words as said so say() does not replay a tail through Gemini,
        and unblock the walkthrough the same way generation_complete does.
        """
        self.__dict__.setdefault("_turn_said", []).append(text)
        self._last_turn_audio_s = float(audio_s or 0.0)
        self._turn_audio_s = 0.0
        self.speaking = False
        self._turn_closed = True
        mark = getattr(self.bridge, "mark_line_end", None)
        if callable(mark):
            mark()
        now = time.monotonic()
        self._gen_done_at = now
        self._drain_until = now + min(MAX_DRAIN_S, max(0.0, float(audio_s or 0.0))) + 0.3
        self._audio_rx_at = now
        self._drop_inflight_audio = False
        if getattr(self, "_narration_chunk_open", False):
            # Barge-in before the first byte: nothing to align on video.
            self._narration_chunk_open = False
        self._turn_done.set()
        self._emit(LiveEvent(kind="said", text=text))
        self._emit(LiveEvent(kind="turn_complete"))

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
