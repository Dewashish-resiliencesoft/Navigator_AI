"""Live meeting demo: bot-first join → intake Q&A → screenshare → demo graph.

Meet and Zoom: tunnel /view → enable_screenshare after join (Zoom needs web SDK).

Order (what the prospect experiences):

  1. Bot joins quietly (voice agent resources reserved)
  2. Join link shared once bot is in the meeting
  3. Wait for human → intake greet + questions + pitch (before screen share)
  4. Browser opens on start page (or login page when login is part of the demo)
     → screenshare armed
  5. Kickoff line → visible login if opted in → agent walkthrough
  6. Leave bot, tear down
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from queue import Empty
from typing import Callable
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from playwright.sync_api import sync_playwright

from navigator.agent.graph import anything_else_entry_state, build_graph
from navigator.agent.state import CallDeps, initial_state
from navigator.automation.browser.cursor import install_cursor, set_screencast_mode
from navigator.automation.browser.login_gate import LoginGateResult, run_login_gate
from navigator.automation.browser.product_login import login_product, open_login_page
from navigator.automation.browser.screen_context import screen_snapshot
from navigator.automation.external_links import url_origin
from navigator.knowledge.product_brief import load_agent_context
from navigator.knowledge.site_graph import load_site_graph
from navigator.logs.store import ActionLog
from navigator.meeting.attendee import AttendeeClient, ParticipantWaitStopped
from navigator.meeting.intake import (
    demo_kickoff_line,
    intake_from_prefill,
    quick_greet_line,
    run_intake,
)
from navigator.meeting.meet_speaker import MeetSpeaker
from navigator.meeting.relay import (
    push_frame,
    start_relay,
    start_screencast,
    stop_screencast,
)
from navigator.meeting.screenshare import arm_screenshare, wait_until_screenshare_live
from navigator.meeting.tunnel import start_tunnel, verify_attendee_docker_dns
from navigator.meeting.zoom_host import is_zoom_meeting, zoom_zak_callback_url
from navigator.core.settings import settings
from navigator.core.usage_context import bind_demo_usage, clear_demo_usage
from navigator.voice.stt import VoiceSegmenter, transcribe
from navigator.voice.tts import PrintSpeaker


def _is_likely_echo(heard: str, bot_text: str) -> bool:
    """True when STT likely captured the bot's own TTS, not the prospect."""
    from navigator.meeting.intake_clean import is_likely_bot_echo

    return is_likely_bot_echo(heard, bot_text)


def _drain_inbound(queue) -> int:
    n = 0
    while True:
        try:
            queue.get_nowait()
            n += 1
        except Empty:
            return n


def _intake_summary(intake) -> str:
    """One line about the prospect, from what intake actually captured."""
    if intake is None:
        return ""
    bits = []
    for label, attr in (
        ("Name", "name"),
        ("Company", "company"),
        ("Business", "business_type"),
        ("Looking for", "looking_for"),
    ):
        val = (getattr(intake, attr, "") or "").strip()
        if val:
            bits.append(f"{label}: {val}")
    return " | ".join(bits)


def _start_live_agent(
    *,
    audio_bridge,
    graph_cfg,
    product_id: str,
    intake,
    spoken_language: str,
    agent_gender: str,
    heard_sink: list[str] | None = None,
    voice_name: str = "",
    live_conversational_model: str = "",
):
    """Open the bidirectional Live session, or return None (caller stops the demo)."""
    if audio_bridge is None:
        print("[live] Live audio needs the audio bridge", flush=True)
        return None

    from navigator.core.gemini_keys import gemini_key_candidates

    keys = gemini_key_candidates()
    if not keys:
        print("[live] Live audio needs a Gemini key", flush=True)
        return None

    def _on_event(event) -> None:
        _log_live_event(event)
        # PLANNING still routes flows and drives the browser, so it needs to
        # know what the prospect asked. LISTENING drains this list first.
        text = (event.text or "").strip() if event.kind == "heard" else ""
        if text and heard_sink is not None:
            heard_sink.append(text)
        # Switch Live language as soon as we hear the request — before the
        # model finishes an English-only refuse (MeetSpeaker was updated alone).
        if text and agent_box:
            from navigator.voice.language import detect_language_switch

            target = detect_language_switch(text)
            if target is not None:
                try:
                    agent_box[0].set_language(target)
                except Exception:  # noqa: BLE001
                    pass

    try:
        from navigator.voice.live_agent import LiveAgent, LiveAgentConfig
        from navigator.voice.live_persona import build_live_instruction

        instruction = build_live_instruction(
            graph=graph_cfg,
            product_brief=load_agent_context(product_id or graph_cfg.site),
            intake_summary=_intake_summary(intake),
            language=spoken_language,  # type: ignore[arg-type]
            gender=agent_gender,
        )
        agent_box: list = []
        last_fail = ""
        for i, key in enumerate(keys):
            agent = LiveAgent(
                LiveAgentConfig(
                    api_key=key,
                    system_instruction=instruction,
                    model=live_conversational_model or settings.live_conversational_model,
                    voice_name=voice_name or settings.gemini_live_voice,
                    language=spoken_language,  # type: ignore[arg-type]
                    vad_silence_ms=settings.live_vad_silence_ms,
                    on_event=_on_event,
                ),
                audio_bridge,
            )
            agent_box[:] = [agent]
            if agent.start(timeout_s=45.0):
                return agent
            last_fail = agent._failed or "start failed"
            print(
                f"[live] Gemini key {i + 1}/{len(keys)} failed ({last_fail})",
                flush=True,
            )
            agent.close()
            agent_box.clear()
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"[live] Live setup failed ({exc})", flush=True)
        return None


def _own_meet_tts_when_live(meet_speaker, live_box: list) -> None:
    """When Live owns audio, route MeetSpeaker.say to live.say (no WAV)."""
    if getattr(meet_speaker, "_live_owns_audio", False):
        return
    from navigator.meeting.playback_handle import PlaybackHandle

    orig_say = meet_speaker.say
    orig_async = getattr(meet_speaker, "say_async", None)

    def _say(text: str, *, mode: str = "natural") -> None:
        live = live_box[0] if live_box else None
        if live is not None:
            live.say(text, mode=mode)
            return
        orig_say(text)

    def _say_async(text: str):
        live = live_box[0] if live_box else None
        if live is None:
            if orig_async is not None:
                return orig_async(text)
            orig_say(text)
            handle = PlaybackHandle()
            handle._finish()
            return handle
        handle = PlaybackHandle()

        def _worker() -> None:
            try:
                live.say(text, mode="natural")
            except Exception as exc:  # noqa: BLE001
                handle.error = str(exc)
            finally:
                handle._finish()

        handle._thread = threading.Thread(
            target=_worker, name="live-own-say", daemon=True
        )
        handle._thread.start()
        return handle

    meet_speaker.say = _say  # type: ignore[method-assign]
    if orig_async is not None:
        meet_speaker.say_async = _say_async  # type: ignore[method-assign]
    meet_speaker._live_owns_audio = True  # type: ignore[attr-defined]
    print("[live] Live owns mic+mouth", flush=True)


def _talk_speaker(meet_speaker, live_box: list):
    """Route say/say_async through Live when the session is up."""

    class _Talk:
        @property
        def last_spoken(self) -> str:
            live = live_box[0] if live_box else None
            if live is not None:
                return getattr(live, "last_spoken", "") or ""
            return getattr(meet_speaker, "last_spoken", "") or ""

        def say(self, text: str, *, mode: str = "natural") -> None:
            live = live_box[0] if live_box else None
            if live is not None:
                live.say(text, mode=mode)
                return
            try:
                meet_speaker.say(text, mode=mode)
            except TypeError:
                meet_speaker.say(text)

        def say_async(self, text: str):
            live = live_box[0] if live_box else None
            if live is not None:
                from navigator.meeting.playback_handle import PlaybackHandle

                handle = PlaybackHandle()

                def _worker() -> None:
                    try:
                        live.say(text, mode="natural")
                    except Exception as exc:  # noqa: BLE001
                        handle.error = str(exc)
                    finally:
                        handle._finish()

                handle._thread = threading.Thread(
                    target=_worker, name="live-talk-say", daemon=True
                )
                handle._thread.start()
                return handle
            return meet_speaker.say_async(text)

        def set_language(self, lang) -> None:
            meet_speaker.set_language(lang)
            live = live_box[0] if live_box else None
            if live is not None and hasattr(live, "set_language"):
                live.set_language(lang)

        def __getattr__(self, name: str):
            return getattr(meet_speaker, name)

    return _Talk()


def select_engine(
    *,
    live_agent_present: bool,
    playlist_demo: bool,
    timeline_ready: bool,
    conversational: bool,
) -> tuple[str, str]:
    """Select runtime engine and expose the branch reason for diagnostics."""
    if live_agent_present:
        return "gemini_live", "Live Agent available"
    if playlist_demo and timeline_ready:
        return "timeline", "playlist metadata complete"
    if playlist_demo:
        return "strict_playlist", "playlist metadata incomplete"
    if conversational:
        return "langgraph_conversational", "no playlist; conversational mode"
    return "langgraph", "no Live Agent and no playlist"


def _log_live_event(event) -> None:
    if event.kind == "said" and event.text.strip():
        print(f"[live] said: {event.text.strip()}", flush=True)
    elif event.kind == "heard" and event.text.strip():
        print(f"[live] heard: {event.text.strip()}", flush=True)
    elif event.kind == "interrupted":
        print("[live] barge-in — prospect is speaking", flush=True)
    elif event.kind == "error":
        print(f"[live] ERROR {event.text}", flush=True)


def _wait_meet_utterance(
    inbound,
    *,
    prompt: str,
    api_key: str,
    timeout_s: float = 60.0,
    audio_bridge=None,
    bot_spoken: str = "",
) -> str:
    """Block until prospect utterance (not bot echo), or timeout → \"\"."""
    deadline = time.monotonic() + timeout_s
    started_chunks = getattr(audio_bridge, "chunks_received", 0) if audio_bridge else 0

    def frames():
        while time.monotonic() < deadline:
            try:
                # Tight poll — 400ms empty waits added dead air after user stopped.
                yield inbound.get(timeout=0.05)
            except Empty:
                continue

    segmenter = VoiceSegmenter(min_silence_ms=settings.live_stt_min_silence_ms)
    for pcm in segmenter.segments(frames()):
        text = (transcribe(pcm, api_key) or "").strip()
        if not text:
            continue
        # Match against the question AND the last TTS line (greeting / ack).
        # Without last_spoken, "I'm Navigator AI" from the greet becomes the
        # prospect's "name".
        if _is_likely_echo(text, prompt) or _is_likely_echo(text, bot_spoken):
            print(f"[intake] ignoring echo: {text!r}", flush=True)
            continue
        return text
    got = getattr(audio_bridge, "chunks_received", 0) if audio_bridge else 0
    print(
        f"[intake] no utterance in {timeout_s:.0f}s "
        f"(pcm_chunks={got - started_chunks}, ws_clients="
        f"{getattr(audio_bridge, 'clients_connected', '?')})",
        flush=True,
    )
    return ""


def assert_live_site_graph(path: Path) -> None:
    text = path.read_text()
    if "tests/fixtures" in text or "crm_dashboard.html" in text:
        raise RuntimeError(
            f"live demo refuses fixture site graph {path}. "
            "Record your product: python -m navigator.automation.record --url $NAVIGATOR_PRODUCT_URL "
            "or upload a live site graph for this client."
        )


def show_login_on_screenshare(
    graph,
    *,
    login_url: str,
    include_login_in_default_flow: bool,
) -> bool:
    """True when the prospect should see the login form before auth runs."""
    from navigator.automation.login_match import playlist_has_login_flow

    if playlist_has_login_flow(graph):
        return False
    if include_login_in_default_flow:
        return True
    playlist = sorted(graph.demo_playlist or [], key=lambda x: x.order)
    if not playlist:
        return False
    first = playlist[0]
    if "login" in first.flow_id.lower():
        return True
    if not login_url:
        return False
    from navigator.automation.login_match import LoginConfig, is_login_url

    return is_login_url(graph.url_for(first.page_id), LoginConfig(login_url=login_url))


def share_media_join_opts(*, is_zoom: bool) -> tuple[bool, str | None]:
    """Join flags for Meet vs Zoom.

    Meet: reserve voice agent → mid-call screenshare via tunnel /view.
    Zoom: web SDK + reserve so screenshare PATCH works (native SDK blocks it).
    """
    if is_zoom:
        return True, "web"
    return True, None


def _attendee_reachable(base_url: str) -> bool:
    from navigator.meeting.attendee_stack import attendee_reachable

    return attendee_reachable(base_url)


def _require_live_settings(meeting_url: str) -> None:
    # Self-hosted Attendee on localhost is the free path, so localhost is valid —
    # but an unconfigured default looks identical, so require it to answer.
    local = any(h in settings.attendee_base_url for h in ("localhost", "127.0.0.1"))
    if local and not _attendee_reachable(settings.attendee_base_url):
        raise RuntimeError(
            f"Attendee unreachable at {settings.attendee_base_url}; Navigator tried "
            "autostart on boot (NAVIGATOR_ATTENDEE_AUTOSTART). Start manually: "
            "docker compose -f dev.docker-compose.yaml -f local.docker-compose.yaml "
            "--profile webpage-streamer up -d in your Attendee clone, or point "
            "NAVIGATOR_ATTENDEE_BASE_URL at https://app.attendee.dev/api/v1"
        )
    missing = [
        name
        for name, val in [
            ("NAVIGATOR_ATTENDEE_API_KEY", settings.attendee_api_key),
            ("meeting_url (pass it in, or set NAVIGATOR_MEETING_URL for the CLI)",
             meeting_url),
        ]
        if not val
    ]
    if is_zoom_meeting(meeting_url) and not settings.public_base_url:
        from navigator.meeting.tunnel import tunnel_binary_available

        if not tunnel_binary_available(settings.tunnel_bin):
            missing.append("NAVIGATOR_PUBLIC_BASE_URL (or a working tunnel_bin)")
    if missing:
        raise RuntimeError(f"missing config for live Meet demo: {', '.join(missing)}")


class LiveDemoStopped(Exception):
    """Operator ended the demo (client dashboard End / API stop)."""


HUMAN_LEAVE_GRACE_S = 25


def next_leave_grace(
    left: bool, remaining: int | None, *, grace_s: int = HUMAN_LEAVE_GRACE_S
) -> int | None:
    """Seconds left before auto-end. None = not in grace (present or cancelled)."""
    if not left:
        return None
    if remaining is None:
        return grace_s
    return max(0, remaining - 1)


def _check_stop(stop_event: threading.Event | None) -> None:
    if stop_event is not None and stop_event.is_set():
        raise LiveDemoStopped("ended by operator")


def _start_human_leave_watcher(
    *,
    client: AttendeeClient,
    bot_id: str,
    human_name: str,
    agent_name: str,
    stop_event: threading.Event | None,
    speaker_box: list,
    on_leave_grace: Callable[[int | None], None] | None = None,
) -> threading.Thread:
    """If the prospect leaves, wait HUMAN_LEAVE_GRACE_S then end so they can bounce."""

    def _run() -> None:
        remaining: int | None = None
        while True:
            if stop_event is not None and stop_event.is_set():
                if on_leave_grace is not None:
                    on_leave_grace(None)
                return
            try:
                left = client.human_has_left(
                    bot_id,
                    human_name=human_name,
                    bot_names=frozenset(
                        {
                            agent_name,
                            "Navigator",
                            "Navigator AI",
                            "Attendee",
                        }
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[live] leave-watch poll skipped: {exc}", flush=True)
                left = False
            nxt = next_leave_grace(left, remaining)
            if nxt != remaining and on_leave_grace is not None:
                on_leave_grace(nxt)
            if nxt is not None and remaining is None:
                print(
                    f"[live] human left meeting ({human_name!r}) — "
                    f"ending in {nxt}s if they stay out",
                    flush=True,
                )
            if remaining is not None and nxt is None:
                print(
                    f"[live] human rejoined ({human_name!r}) — leave countdown cancelled",
                    flush=True,
                )
            remaining = nxt
            if remaining == 0:
                print(
                    f"[live] leave grace elapsed ({human_name!r}) — ending demo",
                    flush=True,
                )
                if stop_event is not None:
                    stop_event.set()
                if speaker_box:
                    try:
                        speaker_box[0].bot_ended = True
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    if client.leave_if_active(bot_id):
                        print(f"[live] bot {bot_id} leave sent (human exited)", flush=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"[live] leave-on-human-exit failed: {exc}", flush=True)
                return
            time.sleep(1.0)

    t = threading.Thread(
        target=_run, name=f"leave-watch-{bot_id}", daemon=True
    )
    t.start()
    return t


def wait_until_joined(
    client: AttendeeClient,
    bot_id: str,
    *,
    timeout_s: float = 180.0,
    stop_event: threading.Event | None = None,
) -> None:
    deadline = time.time() + timeout_s
    last = ""
    warned_waiting = False
    while time.time() < deadline:
        _check_stop(stop_event)
        bot = client.get(bot_id)
        last = bot.raw_state or bot.state
        if bot.state == "joined":
            return
        if bot.state == "fatal_error":
            raise RuntimeError(
                f"Attendee bot fatal_error (last state={last}). "
                "Zoom web SDK error 3712 'Invalid signature' means Attendee's "
                "Meeting SDK JWT is wrong — NAVIGATOR_ZOOM_SDK_CLIENT_ID/SECRET "
                "must be a General App with Meeting SDK, not the Server-to-Server "
                "NAVIGATOR_ZOOM_CLIENT_ID used for create/ZAK. A ZAK callback 200 "
                "does not prove the SDK signature. Other causes: worker DNS for "
                "the ZAK tunnel hostname, or ZAK callback 401/502. "
                "See: docker compose logs attendee-worker-local"
            )
        if "waiting" in last.lower() and not warned_waiting:
            warned_waiting = True
            print(
                "[live] Navigator is in the Meet WAITING ROOM.\n"
                "[live] Bot-first join needs Quick access ON so it can enter alone:\n"
                "[live]   Meet → Host controls → Meeting access → turn ON\n"
                "[live]   'People can join without asking' / Quick access.\n"
                "[live] Or admit Navigator once from another device, then re-run.",
                flush=True,
            )
        time.sleep(0.75)
    raise TimeoutError(
        f"Attendee bot did not join within {timeout_s}s (last={last}). "
        "For bot-first demos, enable Meet Quick access so Navigator enters "
        "without a host admitting them."
    )


def _share_meet_link(*, meeting_url: str, bot_ready: bool) -> None:
    """Print join link — only after bot is in-call when bot_ready=True.

    ponytail: no mailto/Resend here. Email later when user asks.
    """
    print("=" * 60, flush=True)
    if bot_ready:
        print("[live] Navigator is ALREADY in the meeting.", flush=True)
        print("[live] Join this link — you should see Navigator waiting:", flush=True)
    else:
        print("[live] Meet join link:", flush=True)
    print(f"[live]   {meeting_url}", flush=True)
    print("=" * 60, flush=True)


def _resolve_provider_keys(product_id: str | None) -> dict[str, str]:
    out = {
        "gemini": settings.gemini_api_key or "",
        "groq": settings.groq_api_key or "",
        "openai": settings.openai_api_key or "",
        "anthropic": "",
    }
    if not product_id:
        return out
    try:
        from navigator.app.credential_vault import CredentialVault

        with CredentialVault(settings.credential_db_path) as vault:
            for kind in out:
                key = vault.provider_key(product_id, kind)
                if key:
                    out[kind] = key
    except Exception:  # noqa: BLE001
        pass
    return out


def _provider_byok_flags(product_id: str | None) -> tuple[bool, bool]:
    if not product_id:
        return False, False
    try:
        from navigator.app.credential_vault import CredentialVault

        with CredentialVault(settings.credential_db_path) as vault:
            pub = vault.provider_keys_public(product_id)
            return (
                bool(pub.get("has_groq_api_key")),
                bool(pub.get("has_gemini_api_key")),
            )
    except Exception:  # noqa: BLE001
        return False, False


def _leave_stale_bots(client: AttendeeClient, meeting_url: str) -> None:
    try:
        from urllib.request import Request, urlopen
        import json

        req = Request(
            f"{client.base_url}/bots",
            method="GET",
            headers={
                "Authorization": f"Token {client.api_key}",
                "Accept": "application/json",
            },
        )
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read() or b"[]")
    except Exception as exc:  # noqa: BLE001
        print(f"[live] could not list bots to clean up: {exc}", flush=True)
        return

    bots = data if isinstance(data, list) else data.get("results") or data.get("bots") or []
    for raw in bots:
        if not isinstance(raw, dict):
            continue
        if raw.get("meeting_url") != meeting_url:
            continue
        state = str(raw.get("state", ""))
        if state in {"ended", "fatal_error", "post_processing"}:
            continue
        bot_id = str(raw.get("id", ""))
        if not bot_id:
            continue
        try:
            print(f"[live] leaving stale bot {bot_id} ({state})", flush=True)
            client.leave_if_active(bot_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[live] stale leave failed {bot_id}: {exc}", flush=True)


def run_live_meet_demo(
    *,
    meeting_url: str | None = None,
    page_id: str = "inbox",
    flow_id: str = "send_test_message",
    headful: bool = True,
    mute: bool = False,
    interactive_listen: bool | None = None,
    open_meet_in_browser: bool | None = None,
    wait_for_human: bool = True,
    human_join_timeout_s: float = 300.0,
    bot_first: bool | None = None,
    graph_cfg=None,
    product_id: str | None = None,
    session_id=None,
    intake_prefill: dict[str, str] | None = None,
    auto_play: bool = True,
    on_meeting_ready=None,
    stop_event: threading.Event | None = None,
    on_bot_joined: Callable[[str], None] | None = None,
    on_leave_grace: Callable[[int | None], None] | None = None,
    tier2_enabled: bool = False,
    brain_config=None,
    use_turn_brain: bool | None = None,
    handoff_webhook_url: str = "",
    agent_settings=None,
    demo_origin: str = "dashboard_test",
) -> str:
    """Join Meet, qualify prospect, then share screen and run demo. Returns bot id.

    Default: bot joins first, then Meet link is shared (you arrive to Navigator
    already present). Requires Meet Quick access so the bot is not stuck knocking.

    `meeting_url` is passed in by the API (a link created for this session). The
    env var is only the fallback for the standalone CLI run.
    `graph_cfg` lets the API supply a registered product's site graph instead of
    the on-disk NAVIGATOR_SITE_GRAPH.
    """
    if graph_cfg is None:
        assert_live_site_graph(Path(settings.site_graph))
    meeting_url = meeting_url or settings.meeting_url
    _require_live_settings(meeting_url)

    if interactive_listen is None:
        interactive_listen = sys.stdin.isatty()
    if bot_first is None:
        bot_first = settings.live_bot_first
    # Open browser for the *prospect* after bot is in (bot-first), or for host
    # before bot join (legacy admit flow). Default off — print the link instead.
    if open_meet_in_browser is None:
        open_meet_in_browser = settings.open_meet_in_browser

    client = AttendeeClient(settings.attendee_base_url, settings.attendee_api_key)
    if any(h in settings.attendee_base_url for h in ("localhost", "127.0.0.1")):
        from navigator.meeting.attendee_stack import ensure_webpage_streamer

        ensure_webpage_streamer()
    _leave_stale_bots(client, meeting_url)

    from navigator.core.agent_settings import AgentSettings, merge_agent_settings

    if agent_settings is None and product_id:
        try:
            from navigator.app.registry import Registry

            with Registry(settings.db_path) as reg:
                agent_settings = reg.get_agent_settings(product_id)
        except Exception:  # noqa: BLE001
            agent_settings = merge_agent_settings(None)
    elif agent_settings is None:
        agent_settings = merge_agent_settings(None)

    from navigator.core.role_models import resolved_runtime_models

    runtime_models = resolved_runtime_models(agent_settings)

    provider_keys = _resolve_provider_keys(product_id)
    if product_id:
        groq_byok, gemini_byok = _provider_byok_flags(product_id)
        bind_demo_usage(
            product_id=product_id,
            session_id=str(session_id) if session_id else None,
            groq_client=groq_byok,
            gemini_client=gemini_byok,
        )
    spoken_language: str = agent_settings.default_language or settings.default_spoken_language
    print(
        f"[live] spoken language default={spoken_language!r} "
        f"(extras={list(agent_settings.extra_languages)})",
        flush=True,
    )
    speaker = PrintSpeaker()
    if graph_cfg is None:
        graph_cfg = load_site_graph(settings.site_graph)
    persona = graph_cfg.effective_persona()
    if (agent_settings.agent_name or "").strip():
        persona = persona.model_copy(
            update={"agent_name": agent_settings.agent_name.strip()}
        )
    if (agent_settings.tone or "").strip():
        persona = persona.model_copy(update={"tone": agent_settings.tone.strip()})

    relay = start_relay()
    tunnel = None
    audio_bridge = None
    audio_tunnel = None
    screencast = None
    bot_id: str | None = None
    live_box: list = []
    orch_box: list = []

    try:
        zoom_native = is_zoom_meeting(meeting_url)
        public_view: str | None = None

        # Audio tunnel first — join needs the wss URL. Screenshare tunnel waits
        # until after the bot is in-meeting so the join link is usable sooner.
        audio_ws_url = None
        try:
            from navigator.meeting.audio_bridge import AudioBridge

            audio_bridge = AudioBridge().start()
            audio_tunnel = start_tunnel(
                audio_bridge.port, binary=settings.tunnel_bin, ready_path=None
            )
            audio_ws_url = audio_tunnel.public_url.replace("https://", "wss://").replace(
                "http://", "ws://"
            )
            print(f"[live] audio websocket ready: {audio_ws_url}", flush=True)
            audio_host = urlparse(audio_ws_url).hostname or ""
            if audio_host:
                try:
                    verify_attendee_docker_dns(audio_host)
                except RuntimeError as exc:
                    print(f"[live] audio tunnel DNS warn: {exc}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[live] audio bridge skipped: {exc}", flush=True)
            audio_bridge = None
            if audio_tunnel is not None:
                audio_tunnel.stop()
                audio_tunnel = None

        if not bot_first:
            # Legacy: host admits bot from waiting room.
            _share_meet_link(meeting_url=meeting_url, bot_ready=False)
            print(
                "[live] Open Meet as HOST and admit "
                f"{persona.agent_name} if asked (or enable Quick access).",
                flush=True,
            )
            if open_meet_in_browser:
                import webbrowser

                webbrowser.open(meeting_url)
            time.sleep(8)

        if bot_first:
            print(
                "[live] Navigator joining meeting first"
                + (
                    " (Zoom web SDK + ZAK)…"
                    if zoom_native
                    else " (voice reserved; share after)…"
                ),
                flush=True,
            )
        else:
            print(
                "[live] Navigator joining as guest — admit from host UI if asked…",
                flush=True,
            )
        zoom_tokens_url = None
        if zoom_native:
            from navigator.meeting.attendee_stack import ensure_attendee_zoom_credentials
            from navigator.meeting.zoom_host import ensure_public_base_url

            if not ensure_attendee_zoom_credentials():
                ui_hint = ""
                try:
                    from navigator.meeting.attendee_stack import attendee_ui_origin

                    ui_hint = (
                        f"\nOr Attendee dashboard → Project → Credentials "
                        f"({attendee_ui_origin()}/projects/…/credentials)"
                    )
                except Exception:
                    pass
                raise RuntimeError(
                    "Zoom web SDK bot needs Attendee Meeting SDK credentials "
                    "(General App, not the Server-to-Server create/ZAK app). "
                    "Set NAVIGATOR_ZOOM_SDK_CLIENT_ID/SECRET in .env, then run "
                    "./scripts/sync-attendee-zoom-credentials.sh"
                    f"{ui_hint}"
                )

            zoom_tokens_url = zoom_zak_callback_url()
            base = ensure_public_base_url()
            host = urlparse(base).hostname or ""
            if host:
                verify_attendee_docker_dns(host)
            print(
                f"[live] Zoom host ZAK callback: {zoom_tokens_url.split('?', 1)[0]}",
                flush=True,
            )
            print(
                "[live] Zoom: web SDK + ZAK host (screenshare via tunnel after join)",
                flush=True,
            )
        reserve, zoom_sdk = share_media_join_opts(is_zoom=zoom_native)
        bot = client.join(
            meeting_url,
            bot_name=(persona.agent_name or "Navigator AI").strip() or "Navigator AI",
            reserve_voice_agent=reserve,
            audio_websocket_url=audio_ws_url,
            google_meet_use_login=settings.google_meet_use_login,
            zoom_tokens_url=zoom_tokens_url,
            zoom_sdk=zoom_sdk,
        )
        bot_id = bot.id
        if on_bot_joined is not None:
            on_bot_joined(bot_id)
        if audio_bridge is not None:
            client.register_audio_hub(bot.id, audio_bridge.inbound)
        print(f"[live] bot {bot_id} created ({bot.raw_state or bot.state})", flush=True)
        wait_until_joined(client, bot.id, stop_event=stop_event)
        print("[live] bot in meeting", flush=True)
        _check_stop(stop_event)

        # Mark ready whether bot-first or admit-flow — UI/status need the flag.
        if on_meeting_ready is not None:
            on_meeting_ready(meeting_url)

        if bot_first:
            # Link only after Navigator is already inside.
            _share_meet_link(meeting_url=meeting_url, bot_ready=True)
            if open_meet_in_browser:
                import webbrowser

                print(
                    f"[live] opening meeting for you (Navigator already there): "
                    f"{meeting_url}",
                    flush=True,
                )
                webbrowser.open(meeting_url)

        # Screenshare tunnel after bot join (Meet + Zoom web SDK).
        public_agent: str | None = None
        print("[live] starting screenshare tunnel…", flush=True)
        tunnel = start_tunnel(relay.port, binary=settings.tunnel_bin)
        public_view = f"{tunnel.public_url}/view"
        public_agent = f"{tunnel.public_url}/agent"
        print(f"[live] screenshare URL ready: {public_view}", flush=True)
        if tunnel._proc.poll() is not None:
            raise RuntimeError("cloudflared died before screenshare")
        _ = public_agent  # avatar tile path reserved for later

        if audio_bridge is not None:
            print("[live] waiting for Attendee audio websocket…", flush=True)
            deadline = time.time() + settings.live_audio_ws_wait_s
            while time.time() < deadline and audio_bridge.clients_connected < 1:
                _check_stop(stop_event)
                time.sleep(0.25)
            if audio_bridge.clients_connected < 1:
                print(
                    "[live] WARNING: Attendee never connected to audio WS — "
                    "continuing without live meeting audio. Grant Attendee "
                    "recording permission in Zoom if this persists.",
                    flush=True,
                )
            else:
                print(
                    f"[live] audio WS up (clients={audio_bridge.clients_connected})",
                    flush=True,
                )

        def _after_speak() -> None:
            if audio_bridge is None:
                return
            n = _drain_inbound(audio_bridge.inbound)
            if n:
                print(f"[speak] drained {n} echo frame(s)", flush=True)

        pending_barge_in: list[str] = []
        speaker_box: list = []
        meet_speaker = MeetSpeaker(
            speaker,
            client,
            bot.id,
            also_chat=False,
            after_speak=_after_speak,
            set_avatar_state=relay.set_avatar_state,
        )
        speaker_box.append(meet_speaker)
        from navigator.voice.language import apply_to_speakers

        apply_to_speakers(
            spoken_language,  # type: ignore[arg-type]
            speaker,
            meet_speaker,
        )
        if audio_bridge is not None and settings.groq_api_key:
            from navigator.meeting.barge_in import make_barge_in_checker
            from navigator.voice.stt import transcribe as _stt

            def _tx(pcm: bytes) -> str:
                return _stt(pcm, settings.groq_api_key)

            meet_speaker.check_barge_in = make_barge_in_checker(
                audio_bridge.inbound,
                is_bot_echo=lambda t: _is_likely_echo(t, meet_speaker.last_spoken),
                transcribe=_tx,
                pending_barge_in=pending_barge_in,
            )

        talk = _talk_speaker(meet_speaker, live_box)
        speaker_box[0] = talk

        merged_prefill = dict(intake_prefill or {})

        from navigator.agent.speech_safety import prospect_facing_persona

        facing = prospect_facing_persona(
            persona, fallback_product=product_id or graph_cfg.site or ""
        )

        print("[live] waiting for a human participant…", flush=True)
        human_name = ""
        if wait_for_human:
            try:
                human_name = (
                    client.wait_for_human_join(
                        bot.id,
                        timeout_s=human_join_timeout_s,
                        stop_event=stop_event,
                    )
                    or ""
                )
                print(f"[live] human joined: {human_name!r}", flush=True)
                from navigator.meeting.intake import usable_meeting_display_name

                display = usable_meeting_display_name(human_name)
                if display and not (merged_prefill.get("name") or "").strip():
                    merged_prefill["name"] = display
                    print(
                        f"[live] intake name from meeting display: {display!r}",
                        flush=True,
                    )
                settle = max(0.0, settings.live_human_settle_s)
                if settle:
                    print(f"[live] settle {settle:.1f}s before intake…", flush=True)
                    time.sleep(settle)
                relay.set_status("listening", "Getting to know you…")
                _start_human_leave_watcher(
                    client=client,
                    bot_id=bot.id,
                    human_name=human_name or merged_prefill.get("name") or "there",
                    agent_name=persona.agent_name,
                    stop_event=stop_event,
                    speaker_box=speaker_box,
                    on_leave_grace=on_leave_grace,
                )
            except LiveDemoStopped:
                raise
            except ParticipantWaitStopped as exc:
                raise LiveDemoStopped(str(exc)) from exc
            except TimeoutError:
                if interactive_listen:
                    print(
                        "[live] no join event yet — press Enter when the prospect is in Meet",
                        flush=True,
                    )
                    input()
                else:
                    print(
                        "[live] no join event — running intake anyway",
                        flush=True,
                    )

        # Live owns mic+mouth from intake onward (mute = silent PrintSpeaker).
        if not mute and not live_box:
            early_live = _start_live_agent(
                audio_bridge=audio_bridge,
                graph_cfg=graph_cfg,
                product_id=product_id,
                intake=None,
                spoken_language=spoken_language,
                agent_gender=agent_settings.agent_gender,
                heard_sink=pending_barge_in,
                voice_name=agent_settings.effective_gemini_voice(),
                live_conversational_model=runtime_models["live_conversational_model"] or "",
            )
            if early_live is None:
                raise LiveDemoStopped(
                    "Live session failed to start"
                )
            live_box.append(early_live)
            meet_speaker.check_barge_in = None
            _own_meet_tts_when_live(meet_speaker, live_box)

        def _intake_listen(prompt: str) -> str:
            bot_spoken = (
                getattr(talk, "last_spoken", "")
                or getattr(meet_speaker, "last_spoken", "")
                or ""
            )
            timeout_s = float(
                getattr(
                    brain_config,
                    "listen_timeout_s",
                    settings.brain_listen_timeout_s,
                )
                if brain_config is not None
                else settings.brain_listen_timeout_s
            )
            if live_box:
                live = live_box[0]
                if hasattr(live, "drain_heard"):
                    live.drain_heard()
                # Native-audio Live would answer the human itself here. Mute its
                # mouth for the listen window; transcription still flows.
                set_lo = getattr(live, "set_listen_only", None)
                if set_lo is not None:
                    set_lo(True)
                try:
                    # Keep listening past bot-echo transcripts until timeout.
                    deadline = time.monotonic() + timeout_s
                    while time.monotonic() < deadline:
                        remaining = max(0.05, deadline - time.monotonic())
                        text = (live.wait_for_heard(timeout_s=remaining) or "").strip()
                        if not text:
                            return ""
                        if _is_likely_echo(text, prompt) or _is_likely_echo(
                            text, bot_spoken
                        ):
                            print(f"[intake] ignoring echo: {text!r}", flush=True)
                            continue
                        return text
                    return ""
                finally:
                    if set_lo is not None:
                        set_lo(False)
            if live_box:
                return ""
            if audio_bridge is not None and settings.groq_api_key:
                return _wait_meet_utterance(
                    audio_bridge.inbound,
                    prompt=prompt,
                    api_key=settings.groq_api_key,
                    timeout_s=timeout_s,
                    audio_bridge=audio_bridge,
                    bot_spoken=bot_spoken,
                )
            if interactive_listen:
                try:
                    return input(f"[intake] {prompt}\n> ").strip()
                except EOFError:
                    return ""
            return ""

        can_listen = bool(
            live_box
            or (audio_bridge is not None and settings.groq_api_key)
            or interactive_listen
        )
        extra_langs = tuple(agent_settings.extra_languages)
        # Regex clean only — Groq extract after every answer felt stuck.
        intake, spoken_language = run_intake(
            persona=facing,
            speaker=talk,
            interactive=interactive_listen,
            listen=_intake_listen if can_listen else None,
            prefill=merged_prefill or None,
            will_share_screen=True,
            spoken_language=spoken_language,  # type: ignore[arg-type]
            agent_gender=agent_settings.agent_gender,
            extra_languages=extra_langs,  # type: ignore[arg-type]
            fast_extract=True,
        )
        live_now = live_box[0] if live_box else None
        apply_to_speakers(spoken_language, speaker, meet_speaker, live_now)  # type: ignore[arg-type]
        print(f"[live] intake ({spoken_language}): {intake.model_dump()}", flush=True)
        pending_barge_in.clear()
        if live_now is not None:
            set_do = getattr(live_now, "set_director_only", None)
            if set_do is not None:
                set_do(False)
        if live_now is not None:
            summary = _intake_summary(intake)
            if summary:
                live_now.add_context(f"About the person you are talking to: {summary}")
        from navigator.meeting.intake import preferred_flow_id

        hint = preferred_flow_id(intake.looking_for)
        if hint:
            print(f"[live] intake suggests flow {hint!r} for looking_for", flush=True)

        conversational = bool(settings.groq_api_key) and not bool(
            auto_play and graph_cfg.demo_playlist
        )

        audio_frames = None
        if audio_bridge is not None:
            audio_frames = client.audio_stream(bot.id, timeout_s=4.0)
            print("[live] Meet audio STT armed", flush=True)

        session_id = session_id or uuid4()
        live_opening_done = True
        with ActionLog(settings.db_path) as log, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not headful)
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                device_scale_factor=1,
            )
            page = context.new_page()
            install_cursor(page)

            def _do_login(*, url: str, email: str, password: str, **_kw) -> None:
                login_product(
                    page,
                    url=url,
                    email=email,
                    password=password,
                    visible=show_login,
                    skip_open=show_login,
                    on_progress=_push if show_login else None,
                )

            def _real_origin() -> str:
                """Prefer site-graph base_url; fall back to NAVIGATOR_PRODUCT_URL."""
                for candidate in (graph_cfg.base_url, settings.product_url):
                    c = (candidate or "").strip()
                    if not c.startswith(("http://", "https://")):
                        continue
                    low = c.lower()
                    if "example.com" in low or "fixtures" in low or c.endswith(".html"):
                        continue
                    return c if c.endswith("/") else c + "/"
                raise RuntimeError(
                    "set product domain in the client dashboard "
                    "(Live Demo → Product domain), or NAVIGATOR_PRODUCT_URL"
                )

            origin = _real_origin()
            # The graph may carry a stale/placeholder base_url. Anything that
            # compares the live URL against it (external-link revert, page
            # resolution) would then treat every product page as off-origin and
            # yank the shared screen back after each action.
            if url_origin(graph_cfg.base_url) != url_origin(origin):
                graph_cfg = graph_cfg.model_copy(update={"base_url": origin})
            # Prefer per-product vault; fall back to legacy process-wide env for
            # CLI smoke / single-tenant local runs.
            login_email = ""
            login_password = ""
            login_url = ""
            include_login_in_flow = False
            if product_id:
                try:
                    from navigator.app.credential_vault import (
                        CredentialVault,
                        VaultNotConfigured,
                    )

                    with CredentialVault(settings.credential_db_path) as vault:
                        creds = vault.credentials_for(product_id)
                        if creds is not None:
                            login_url, login_email, login_password = creds
                        include_login_in_flow = vault.include_login_in_default_flow(
                            product_id
                        )
                except VaultNotConfigured:
                    print(
                        "[live] credential vault not configured — "
                        "falling back to NAVIGATOR_PRODUCT_LOGIN_*",
                        flush=True,
                    )
            if not (login_email and login_password):
                login_email = settings.product_login_email
                login_password = settings.product_login_password
            from navigator.automation.login_match import (
                demo_playlist_for_toggle,
                live_start_flow,
                playlist_has_login_flow,
                same_page_path,
            )

            kept = demo_playlist_for_toggle(
                graph_cfg, include_login=include_login_in_flow
            )
            if kept != graph_cfg.demo_playlist:
                graph_cfg = graph_cfg.model_copy(update={"demo_playlist": kept})
                print(
                    f"[live] login toggle={'on' if include_login_in_flow else 'off'} "
                    f"playlist={[i.flow_id for i in kept]}",
                    flush=True,
                )

            playlist_login = playlist_has_login_flow(graph_cfg)
            if auto_play:
                page_id, flow_id = live_start_flow(
                    graph_cfg,
                    page_id,
                    flow_id,
                    include_login=include_login_in_flow,
                )
            start_spec = graph_cfg.page(page_id)
            hold_url = urljoin(origin, start_spec.url.lstrip("/") or "/")

            show_login = bool(
                login_email
                and login_password
                and not playlist_login
                and show_login_on_screenshare(
                    graph_cfg,
                    login_url=login_url or "",
                    include_login_in_default_flow=include_login_in_flow,
                )
            )

            if playlist_login:
                print(
                    "[live] playlist includes a login walkthrough — "
                    "skipping pre-demo sign-in; flows run as recorded",
                    flush=True,
                )
                print(f"[live] opening start page: {hold_url}", flush=True)
                page.goto(hold_url, wait_until="domcontentloaded", timeout=60_000)
            elif login_email and login_password:
                if not login_url:
                    login_url = settings.product_url.strip() or origin
                if "fixtures" in login_url or login_url.endswith(".html"):
                    login_url = origin
                if show_login:
                    print(
                        f"[live] opening login page for screenshare: {login_url}",
                        flush=True,
                    )
                    open_login_page(page, url=login_url)
                else:
                    gate = run_login_gate(
                        login_fn=_do_login,
                        url=login_url,
                        email=login_email,
                        password=login_password,
                        speaker=None,
                        attendee=None,
                        bot_id=None,
                    )
                    if gate is LoginGateResult.failed:
                        print(
                            "[live] login gate failed — aborting before Planning",
                            flush=True,
                        )
                        context.close()
                        browser.close()
                        return bot_id or ""
                    if not same_page_path(page.url, hold_url):
                        print(f"[live] opening start page: {hold_url}", flush=True)
                        page.goto(
                            hold_url, wait_until="domcontentloaded", timeout=60_000
                        )
            else:
                print(f"[live] opening start page: {hold_url}", flush=True)
                page.goto(hold_url, wait_until="domcontentloaded", timeout=60_000)
            print(f"[live] demo page ready: {page.url}", flush=True)

            def _push() -> None:
                try:
                    push_frame(relay, page)
                except Exception as exc:  # noqa: BLE001
                    print(f"[live] frame push skipped: {exc}", flush=True)

            # Paint frames so /view is not blank when Attendee opens it.
            for _ in range(3):
                _push()
                time.sleep(0.08)

            # Stream repaints instead of polling screenshots: the cursor then
            # animates in-page at ~60fps and the audio thread stops paying ~30ms
            # per hop. Falls back to _push automatically if CDP is unavailable.
            screencast = start_screencast(relay, page)
            set_screencast_mode(screencast is not None)

            baseline_hits = relay.frame_hits
            print("[live] enabling screen share…", flush=True)
            relay.set_status("thinking", "Sharing screen…")
            arm_screenshare(client=client, bot_id=bot.id, public_view=public_view)
            live = wait_until_screenshare_live(
                relay,
                push_frame=_push,
                baseline_frame_hits=baseline_hits,
                min_frame_hits=4,
                timeout_s=45.0,
                settle_s=1.0,
            )
            if not live:
                print("[live] screenshare slow — continuing demo anyway", flush=True)

            relay.set_status("speaking", "Starting demo…")
            try:
                # Live owns mouth when conversational — never Piper/MeetSpeaker here.
                talk.say(demo_kickoff_line(lang=spoken_language))  # type: ignore[arg-type]
            except Exception as exc:  # noqa: BLE001
                print(f"[live] kickoff speak skipped: {exc}", flush=True)

            if show_login:
                print("[live] signing in on screenshare…", flush=True)
                relay.set_status("speaking", "Signing in…")
                try:
                    talk.say(
                        "Signing into your product with the saved demo credentials."
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[live] login intro speak skipped: {exc}", flush=True)
                gate = run_login_gate(
                    login_fn=_do_login,
                    url=login_url,
                    email=login_email,
                    password=login_password,
                    speaker=talk,
                    attendee=None,
                    bot_id=None,
                )
                if gate is LoginGateResult.failed:
                    print(
                        "[live] login gate failed — aborting after screenshare",
                        flush=True,
                    )
                    context.close()
                    browser.close()
                    return bot_id or ""
                if not same_page_path(page.url, hold_url):
                    print(f"[live] opening start page: {hold_url}", flush=True)
                    page.goto(hold_url, wait_until="domcontentloaded", timeout=60_000)
                for _ in range(3):
                    _push()
                    time.sleep(0.08)

            from navigator.automation.login_match import LoginConfig

            _login_cfg = LoginConfig(login_url=login_url or "")
            _login_email = login_email
            _login_password = login_password
            _login_url = login_url or origin

            def _resolve_password() -> str | None:
                if _login_password:
                    return _login_password
                if not product_id:
                    return None
                try:
                    from navigator.app.credential_vault import (
                        CredentialVault,
                        VaultNotConfigured,
                    )

                    with CredentialVault(settings.credential_db_path) as vault:
                        return vault.password_for(product_id)
                except VaultNotConfigured:
                    return None

            def _relogin() -> bool:
                if not (_login_email and _login_password):
                    return False
                gate = run_login_gate(
                    login_fn=_do_login,
                    url=_login_url,
                    email=_login_email,
                    password=_login_password,
                    speaker=None,  # silent — verifying already spoke the stall
                    attendee=None,
                    bot_id=None,
                )
                return gate is LoginGateResult.ok

            _pid = product_id or graph_cfg.site or "default"

            def _schedule_prefetch(utterance: str) -> None:
                if not utterance.strip():
                    return

                def _run() -> None:
                    try:
                        from navigator.agent.brain_router import prefetch_context
                        from navigator.knowledge.context import flow_text

                        pg = graph_cfg.page(page_id)
                        texts = {fid: flow_text(fid) for fid in pg.flows}
                        prefetch_context(
                            product_id=_pid,
                            base_query=utterance,
                            flow_texts=texts,
                            chroma_path=settings.chroma_path,
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"[live] prefetch skipped: {exc}", flush=True)

                threading.Thread(target=_run, daemon=True).start()

            def _listen_once(prompt: str) -> str:
                """STT/stdin for requires_live_input FillField pauses."""
                from types import SimpleNamespace

                print(f"[live_input] {prompt}", flush=True)
                live = live_box[0] if live_box else None
                if live is not None or audio_frames is not None:
                    from navigator.agent.nodes.listening import _from_audio

                    try:
                        return (
                            _from_audio(
                                SimpleNamespace(
                                    audio_frames=audio_frames,
                                    transcribe_audio=None,
                                    live_agent=live,
                                    pending_barge_in=pending_barge_in,
                                    is_bot_echo=lambda t: _is_likely_echo(
                                        t, meet_speaker.last_spoken
                                    ),
                                    stop_event=stop_event,
                                    speaker=meet_speaker,
                                ),
                                silence_timeout=12.0,
                            )
                            or ""
                        ).strip()
                    except Exception as exc:  # noqa: BLE001
                        print(f"[live_input] audio listen failed: {exc}", flush=True)
                if interactive_listen:
                    try:
                        return input("[live_input] > ").strip()
                    except EOFError:
                        return ""
                return ""

            meet_speaker.stop_event = stop_event  # type: ignore[attr-defined]

            playlist_demo = bool(auto_play and graph_cfg.demo_playlist)
            live_agent = live_box[0] if live_box else None

            if live_agent is None and not mute:
                live_agent = _start_live_agent(
                    audio_bridge=audio_bridge,
                    graph_cfg=graph_cfg,
                    product_id=product_id,
                    intake=intake,
                    spoken_language=spoken_language,
                    agent_gender=agent_settings.agent_gender,
                    heard_sink=pending_barge_in,
                    voice_name=agent_settings.effective_gemini_voice(),
                    live_conversational_model=runtime_models["live_conversational_model"] or "",
                )
                if live_agent is None:
                    raise LiveDemoStopped(
                        "Live session failed to start"
                    )
                live_box.append(live_agent)
                meet_speaker.check_barge_in = None
                _own_meet_tts_when_live(meet_speaker, live_box)
            elif live_agent is not None:
                meet_speaker.check_barge_in = None
                _own_meet_tts_when_live(meet_speaker, live_box)

            # Live owns listen+decide. Scripted timeline/YAML is TTS-era.
            strict_playlist = bool(graph_cfg.demo_playlist) and live_agent is None
            if strict_playlist:
                tier2_enabled = False
                use_turn_brain = False

            deps = CallDeps(
                graph=graph_cfg,
                page=page,
                log=log,
                speaker=talk,
                scripted_flow=(
                    None
                    if conversational or playlist_demo or live_agent is not None or not flow_id
                    else (page_id, flow_id)
                ),
                product_id=product_id or graph_cfg.site or "default",
                archive_dir=Path("archives"),
                groq_api_key=provider_keys["groq"] or None,
                meeting_url=None,
                attendee=client,
                bot_id=bot.id,
                voice_agent_url=public_agent,
                push_frame=_push,
                get_frame_hits=lambda: relay.frame_hits,
                interactive_listen=interactive_listen,
                audio_frames=audio_frames,
                intake=intake,
                is_bot_echo=lambda t: _is_likely_echo(t, talk.last_spoken),
                set_status=lambda mode, label=None: relay.set_status(mode, label),
                set_avatar_state=relay.set_avatar_state,
                screen_context=lambda: screen_snapshot(page),
                product_brief=load_agent_context(product_id or graph_cfg.site),
                pending_barge_in=pending_barge_in,
                resolve_password=_resolve_password,
                login_config=_login_cfg,
                relogin=_relogin if (_login_email and _login_password) else None,
                stop_event=stop_event,
                listen_once=_listen_once,
                tier2_enabled=tier2_enabled,
                brain_config=brain_config,
                use_turn_brain=use_turn_brain,
                handoff_webhook_url=handoff_webhook_url,
                decision_db_path=settings.db_path,
                on_user_utterance=_schedule_prefetch,
                spoken_language=spoken_language,  # type: ignore[arg-type]
                extra_languages=tuple(agent_settings.extra_languages),
                agent_gender=agent_settings.agent_gender,
                live_opening_done=live_opening_done,
                playlist_only=bool(graph_cfg.demo_playlist),
                auto_advance_walkthrough=bool(
                    auto_play and graph_cfg.demo_playlist
                ),
                strict_playlist=strict_playlist,
                demo_origin=(
                    demo_origin
                    if demo_origin in ("dashboard_test", "public_embed")
                    else "dashboard_test"
                ),  # type: ignore[arg-type]
                live_agent=live_agent,
            )

            from navigator.agent_runtime.bridge import attach_to_deps, build_orchestrator

            revision_id = int(getattr(graph_cfg, "revision", 0) or 0)
            orchestrator = build_orchestrator(
                session_id=session_id,
                product_id=product_id or graph_cfg.site or "default",
                revision_id=revision_id,
                origin=(
                    demo_origin
                    if demo_origin in ("dashboard_test", "public_embed")
                    else "dashboard_test"
                ),
                deps=deps,
            )
            attach_to_deps(deps, orchestrator)
            if orchestrator is not None:
                orch_box.append(orchestrator)
                print("[live] agent runtime orchestrator active", flush=True)

            from navigator.agent.recorded_playback import (
                playlist_timeline_ready,
                run_playlist_timeline,
            )

            timeline_ready = playlist_timeline_ready(graph_cfg)
            engine, engine_reason = select_engine(
                live_agent_present=live_agent is not None,
                playlist_demo=playlist_demo,
                timeline_ready=timeline_ready,
                conversational=conversational,
            )
            use_timeline = engine == "timeline"
            from navigator.agent.demo_trace import emit_demo_trace

            emit_demo_trace(
                None,
                session_id=session_id,
                product_id=product_id or graph_cfg.site or "default",
                event="engine_selected",
                engine=engine,
                reason=engine_reason,
                live_agent_present=live_agent is not None,
                playlist_demo=playlist_demo,
                timeline_ready=timeline_ready,
                conversational=conversational,
            )
            if live_agent is not None:
                mode = "live listen+decide"
            elif conversational:
                mode = "conversational (LLM flow / handoff)"
            else:
                mode = f"scripted {page_id}/{flow_id}"
            if use_timeline:
                engine_label = "timeline playback for narrated playlist"
            elif playlist_demo and live_agent is None:
                engine_label = "strict YAML replay for demo playlist"
            else:
                engine_label = f"demo graph ({mode})"
            print(f"[live] running demo ({mode}) engine={engine_label}", flush=True)
            # Intake answers + kickoff echo must not look like a mid-demo ask.
            pending_barge_in.clear()
            _check_stop(stop_event)
            if use_timeline:
                print("[live] timeline playback for narrated playlist", flush=True)
                final = run_playlist_timeline(
                    deps,
                    session_id=session_id,
                    auto_play=auto_play,
                    strict=strict_playlist,
                )
            elif playlist_demo and live_agent is None:
                print("[live] strict YAML replay for demo playlist", flush=True)
                from navigator.agent.recorded_playback import run_playlist_strict

                final = run_playlist_strict(
                    deps,
                    session_id=session_id,
                    auto_play=auto_play,
                    strict=strict_playlist,
                )
            else:
                print(f"[live] running demo graph ({mode})", flush=True)
                final = build_graph(deps).invoke(
                    initial_state(
                        session_id,
                        page_id,
                        max_turns=settings.live_max_turns,
                        walkthrough_flow_id=flow_id or settings.live_walkthrough_flow,
                        auto_play=auto_play,
                    )
                )
            _push()
            fail_entries = list(final.get("failures") or [])
            failures = len(fail_entries)
            print(
                f"[live] demo finished: actions={len(final.get('entries') or [])} "
                f"failures={failures}",
                flush=True,
            )
            if fail_entries:
                try:
                    from navigator.agent.nodes.reflecting import reflecting

                    reflecting(
                        {"failures": fail_entries, "session_id": session_id},
                        deps,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[live] reflect skipped: {exc}", flush=True)
            stopped = stop_event is not None and stop_event.is_set()
            if not stopped and (use_timeline or (playlist_demo and live_agent is None)):
                qa_page = page_id
                if graph_cfg.demo_playlist:
                    qa_page = max(
                        graph_cfg.demo_playlist, key=lambda item: item.order
                    ).page_id
                print("[live] post-demo Q&A", flush=True)
                if live_box:
                    qa_live = live_box[0]
                    set_do = getattr(qa_live, "set_director_only", None)
                    if set_do is not None:
                        set_do(False)
                    set_lo = getattr(qa_live, "set_listen_only", None)
                    if set_lo is not None:
                        set_lo(False)
                    add_ctx = getattr(qa_live, "add_context", None)
                    if add_ctx is not None:
                        add_ctx(
                            "The product walkthrough is done. You are now in live "
                            "Q&A. Answer the person's questions about this product "
                            "immediately, in the language they are speaking, in one "
                            "or two short sentences. Do not narrate the screen "
                            "unless they ask. Stay on this product only."
                        )
                qa_state = anything_else_entry_state(
                    session_id,
                    qa_page,
                    max_turns=settings.live_max_turns,
                    walkthrough_flow_id=flow_id or settings.live_walkthrough_flow,
                )
                final = build_graph(deps, entry="speaking").invoke(qa_state)
                _push()

            context.close()
            browser.close()
    finally:
        clear_demo_usage()
        if bot_id is not None:
            try:
                if client.leave_if_active(bot_id):
                    print(f"[live] leaving Meet (bot {bot_id})", flush=True)
                else:
                    print(
                        f"[live] bot {bot_id} already left or shutting down — skip leave",
                        flush=True,
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"[live] leave failed: {exc}", flush=True)
        for _live in live_box:
            try:
                _live.close()
            except Exception as exc:  # noqa: BLE001
                print(f"[live] Live session close skipped: {exc}", flush=True)
        for _orch in orch_box:
            try:
                _orch.close()
            except Exception as exc:  # noqa: BLE001
                print(f"[live] orchestrator close skipped: {exc}", flush=True)
        if audio_tunnel is not None:
            audio_tunnel.stop()
        if audio_bridge is not None:
            audio_bridge.stop()
        if tunnel is not None:
            tunnel.stop()
        stop_screencast(screencast)
        set_screencast_mode(False)
        relay.stop()
        if hasattr(speaker, "close"):
            try:
                speaker.close()  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001
                print(f"[live] Live close skipped: {exc}", flush=True)

    return bot_id or ""


def run_live_meet_smoke(**kwargs) -> str:
    return run_live_meet_demo(**kwargs)


def run_login_only(*, headful: bool = False) -> int:
    """Headless (default) login smoke. Exit 0 pass / 1 fail. No Meet."""
    email = settings.product_login_email
    password = settings.product_login_password
    url = settings.product_url
    if not (email and password and url):
        print(
            "[login-only] need NAVIGATOR_PRODUCT_URL + LOGIN_EMAIL + LOGIN_PASSWORD",
            flush=True,
        )
        return 1
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headful)
        page = browser.new_page()
        try:
            login_product(page, url=url, email=email, password=password)
            print("[live] login=pass", flush=True)
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"[live] login=fail err={exc!r}", flush=True)
            return 1
        finally:
            browser.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Navigator live Meet demo")
    parser.add_argument("--login-only", action="store_true")
    parser.add_argument("--headful", action="store_true")
    parser.add_argument(
        "--meeting-url",
        default=None,
        help="Join this meeting. Default: NAVIGATOR_MEETING_URL.",
    )
    parser.add_argument(
        "--create-meeting",
        action="store_true",
        help="Mint a fresh link first (NAVIGATOR_MEETING_PLATFORM), like the API does.",
    )
    args = parser.parse_args()
    if args.login_only:
        raise SystemExit(run_login_only(headful=args.headful))
    url = args.meeting_url
    if args.create_meeting:
        from navigator.meeting.providers import make_provider

        info = make_provider().create_meeting("cli")
        print(f"[live] created {info.platform} meeting: {info.url}", flush=True)
        url = info.url
    print(run_live_meet_demo(meeting_url=url))
