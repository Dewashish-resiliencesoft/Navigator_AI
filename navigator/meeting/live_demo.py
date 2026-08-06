"""Live meeting demo: join quietly → intake → screenshare → agent demo graph.

Meet and Zoom web SDK share the same media path (tunnel → /view screenshare).
Zoom: web SDK + ZAK host role (Attendee native SDK has no screenshare).

Order (what the prospect experiences):

  1. Bot joins *without* screen share (resources reserved)
  2. Console prints join link
  3. Wait until a human participant joins
  4. Greet + ask name, company, business, what they're looking for
  5. Pitch the wrapped product from the site-graph persona
  6. Enable screenshare of Playwright, run intro→listen→plan→execute→verify
  7. Leave bot, tear down

Listening: STT when audio hub registered; else stdin when TTY; else scripted.
Planning: Groq picks named flow or handoff when NAVIGATOR_GROQ_API_KEY set.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from queue import Empty
from typing import Callable
from urllib.parse import urljoin
from uuid import uuid4

from playwright.sync_api import sync_playwright

from navigator.agent.graph import build_graph
from navigator.agent.state import CallDeps, initial_state
from navigator.automation.browser.cursor import install_cursor
from navigator.automation.browser.login_gate import LoginGateResult, run_login_gate
from navigator.automation.browser.product_login import login_product
from navigator.automation.browser.screen_context import screen_snapshot
from navigator.knowledge.product_brief import load_agent_context
from navigator.knowledge.site_graph import load_site_graph
from navigator.logs.store import ActionLog
from navigator.meeting.attendee import AttendeeClient, ParticipantWaitStopped
from navigator.meeting.intake import run_intake
from navigator.meeting.meet_speaker import MeetSpeaker
from navigator.meeting.relay import push_frame, start_relay
from navigator.meeting.screenshare import arm_screenshare, wait_until_screenshare_live
from navigator.meeting.tunnel import start_tunnel
from navigator.meeting.zoom_host import is_zoom_meeting, zoom_zak_callback_url
from navigator.core.settings import settings
from navigator.voice.stt import VoiceSegmenter, transcribe
from navigator.voice.fish_tts import FishSpeaker
from navigator.voice.tts import PiperSpeaker, PrintSpeaker, make_speaker


def _is_likely_echo(heard: str, bot_text: str) -> bool:
    """True when STT likely captured the bot's own TTS, not the prospect."""
    h = " ".join((heard or "").lower().split())
    b = " ".join((bot_text or "").lower().split())
    if len(h) < 3 or not b:
        return False
    if h in b or b in h:
        return True
    hw, bw = set(h.split()), set(b.split())
    if len(hw) < 2:
        return h in b
    return len(hw & bw) / len(hw) >= 0.65


def _drain_inbound(queue) -> int:
    n = 0
    while True:
        try:
            queue.get_nowait()
            n += 1
        except Empty:
            return n


def _wait_meet_utterance(
    inbound,
    *,
    prompt: str,
    api_key: str,
    timeout_s: float = 60.0,
    audio_bridge=None,
) -> str:
    """Block until prospect utterance (not bot echo), or timeout → \"\"."""
    deadline = time.monotonic() + timeout_s
    started_chunks = getattr(audio_bridge, "chunks_received", 0) if audio_bridge else 0

    def frames():
        while time.monotonic() < deadline:
            try:
                yield inbound.get(timeout=0.4)
            except Empty:
                continue

    for pcm in VoiceSegmenter().segments(frames()):
        text = (transcribe(pcm, api_key) or "").strip()
        if not text:
            continue
        if _is_likely_echo(text, prompt):
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


def share_media_join_opts(*, is_zoom: bool) -> tuple[bool, str | None]:
    """Join flags for Meet vs Zoom.

    Meet: reserve voice agent → screenshare after join.
    Zoom: web SDK + ``reserve_resources`` + ZAK (host role in Attendee web
    adapter). Native SDK has no voice-agent/screenshare support.
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
        # Empty is OK locally — zoom_zak_callback_url() auto-tunnels :8000.
        # Still flag it when cloudflared is missing so the error is early.
        from shutil import which
        from pathlib import Path

        tunnel = settings.tunnel_bin
        if tunnel != "cloudflared" and not Path(tunnel).is_file() and not which(tunnel):
            missing.append("NAVIGATOR_PUBLIC_BASE_URL (or a working tunnel_bin)")
    if missing:
        raise RuntimeError(f"missing config for live Meet demo: {', '.join(missing)}")


class LiveDemoStopped(Exception):
    """Operator ended the demo (client dashboard End / API stop)."""


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
) -> threading.Thread:
    """When the prospect leaves Meet, kill the demo so they cannot rejoin mid-run."""

    def _run() -> None:
        poll_s = 2.0
        while True:
            if stop_event is not None and stop_event.is_set():
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
            if left:
                print(
                    f"[live] human left Meet ({human_name!r}) — "
                    "ending demo, bot leaving now",
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
                    client.leave(bot_id)
                except Exception as exc:  # noqa: BLE001
                    print(f"[live] leave-on-human-exit failed: {exc}", flush=True)
                return
            time.sleep(poll_s)

    t = threading.Thread(
        target=_run, name=f"leave-watch-{bot_id}", daemon=True
    )
    t.start()
    return t


def _fatal_error_hint(client: AttendeeClient, bot_id: str) -> str:
    """Turn Attendee bot events into an actionable message."""
    try:
        raw = client._request("GET", f"/bots/{bot_id}")
    except Exception:  # noqa: BLE001
        return (
            "Check Attendee worker logs. Zoom 3712 = wrong Meeting SDK OAuth app — "
            "set NAVIGATOR_ATTENDEE_ZOOM_CLIENT_ID/SECRET (General OAuth + Meeting SDK)."
        )
    events = raw.get("events") if isinstance(raw, dict) else None
    if not isinstance(events, list):
        return (
            "Check Attendee worker logs. Zoom 3712 = wrong Meeting SDK OAuth app — "
            "set NAVIGATOR_ATTENDEE_ZOOM_CLIENT_ID/SECRET (General OAuth + Meeting SDK)."
        )
    for ev in reversed(events):
        if not isinstance(ev, dict):
            continue
        sub = str(ev.get("sub_type") or "")
        if "zoom" in sub:
            return (
                "Zoom join failed (likely error 3712 Signature is invalid). "
                "Attendee needs NAVIGATOR_ATTENDEE_ZOOM_CLIENT_ID/SECRET from a "
                "General OAuth app with Meeting SDK enabled — not the S2S app."
            )
        if ev.get("type") == "could_not_join_meeting":
            return (
                "Bot could not join the meeting. For Zoom, set "
                "NAVIGATOR_ATTENDEE_ZOOM_CLIENT_ID/SECRET (Meeting SDK OAuth app)."
            )
    return "Check Attendee worker logs (docker compose logs attendee-worker-local)."


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
            hint = _fatal_error_hint(client, bot_id)
            raise RuntimeError(
                f"Attendee bot fatal_error (last state={last}). {hint}".rstrip()
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
        time.sleep(2)
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


def _speaker(*, mute: bool):
    return make_speaker(
        mute=mute,
        fish_api_key=settings.fish_api_key,
        fish_model=settings.fish_model,
        fish_reference_id=settings.fish_reference_id,
        tts_provider=settings.tts_provider,
        piper_voice=settings.piper_voice,
        piper_data_dir=settings.piper_data_dir,
    )


def _require_tts_for_meet(*, mute: bool):
    """Live Meet needs Fish (preferred) or Piper WAV → Attendee speak."""
    return make_speaker(
        mute=mute,
        fish_api_key=settings.fish_api_key,
        fish_model=settings.fish_model,
        fish_reference_id=settings.fish_reference_id,
        tts_provider=settings.tts_provider,
        piper_voice=settings.piper_voice,
        piper_data_dir=settings.piper_data_dir,
        require_audio=True,
    )


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
            client.leave(bot_id)
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
    tier2_enabled: bool = False,
    brain_config=None,
    use_turn_brain: bool | None = None,
    handoff_webhook_url: str = "",
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
    _leave_stale_bots(client, meeting_url)
    speaker = _require_tts_for_meet(mute=mute)
    # Warm synthesizer so first Meet utterance isn't cold.
    if hasattr(speaker, "synthesize_mp3"):
        try:
            speaker.synthesize_mp3("Ready.")  # type: ignore[union-attr]
            kind = type(speaker).__name__
            print(f"[live] TTS warmed ({kind}, mp3)", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[live] TTS warm skipped: {exc}", flush=True)
    elif hasattr(speaker, "synthesize_wav"):
        try:
            speaker.synthesize_wav("Ready.")  # type: ignore[union-attr]
            kind = type(speaker).__name__
            print(f"[live] TTS warmed ({kind})", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[live] TTS warm skipped: {exc}", flush=True)
    if graph_cfg is None:
        graph_cfg = load_site_graph(settings.site_graph)
    persona = graph_cfg.effective_persona()

    relay = start_relay()
    tunnel = None
    audio_bridge = None
    audio_tunnel = None
    bot_id: str | None = None

    try:
        is_zoom = is_zoom_meeting(meeting_url)
        reserve, zoom_sdk = share_media_join_opts(is_zoom=is_zoom)
        supports_screenshare = not is_zoom or zoom_sdk == "web"
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
                    " (Zoom web SDK + ZAK host — do NOT join as host yourself)…"
                    if is_zoom
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
        if is_zoom:
            from navigator.meeting.attendee_setup import AttendeeSetupError, sync_attendee_zoom_credentials

            try:
                sync_attendee_zoom_credentials()
            except AttendeeSetupError as exc:
                raise RuntimeError(str(exc)) from exc
            zoom_tokens_url = zoom_zak_callback_url()
            # Tunnel may need a few more seconds after ensure_public_base_url's
            # probe loop; don't abort the demo on one flaky dig — Attendee will
            # hit the callback when it needs the ZAK.
            from navigator.meeting.zoom_host import _zak_origin_reachable, ensure_public_base_url

            base = ensure_public_base_url()
            if not _zak_origin_reachable(base):
                print(
                    f"[live] WARN: ZAK probe failed for {base}/v1/zoom/zak — "
                    "continuing; if Zoom waits for host, set "
                    "NAVIGATOR_PUBLIC_BASE_URL to a stable public origin",
                    flush=True,
                )
            print(
                f"[live] Zoom host ZAK callback: {zoom_tokens_url.split('?', 1)[0]}",
                flush=True,
            )
            print(
                "[live] Zoom: web SDK + ZAK host + voice-agent reserve (screenshare after intake)",
                flush=True,
            )
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

        if audio_bridge is not None:
            # Attendee retries WS up to ~60s; wait so intake isn't deaf.
            print("[live] waiting for Attendee audio websocket…", flush=True)
            deadline = time.time() + 45
            while time.time() < deadline and audio_bridge.clients_connected < 1:
                _check_stop(stop_event)
                time.sleep(0.5)
            if audio_bridge.clients_connected < 1:
                print(
                    "[live] WARNING: Attendee never connected to audio WS — "
                    "voice listen will fail. Check tunnel / wss URL.",
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
        meet_speaker: MeetSpeaker | PrintSpeaker | PiperSpeaker | FishSpeaker = MeetSpeaker(
            speaker,
            client,
            bot.id,
            synthesizer=speaker if hasattr(speaker, "synthesize_wav") else None,
            also_chat=False,
            after_speak=_after_speak,
            set_avatar_state=relay.set_avatar_state,
        )
        speaker_box.append(meet_speaker)
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
                _start_human_leave_watcher(
                    client=client,
                    bot_id=bot.id,
                    human_name=human_name or "there",
                    agent_name=persona.agent_name,
                    stop_event=stop_event,
                    speaker_box=speaker_box,
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
                        "[live] no join event (non-interactive) — continuing intake",
                        flush=True,
                    )

        intake_listen = None
        if audio_bridge is not None and settings.groq_api_key:
            print("[live] intake will wait for your voice answers", flush=True)

            def intake_listen(prompt: str) -> str:
                return _wait_meet_utterance(
                    audio_bridge.inbound,
                    prompt=prompt,
                    api_key=settings.groq_api_key,
                    timeout_s=60.0,
                    audio_bridge=audio_bridge,
                )

        merged_prefill = dict(intake_prefill or {})
        if human_name and "name" not in merged_prefill:
            merged_prefill["name"] = human_name

        print("[live] starting intake (voice into Meet)…", flush=True)
        _check_stop(stop_event)
        relay.set_status("listening", "Listening…")
        from navigator.agent.speech_safety import prospect_facing_persona

        intake = run_intake(
            persona=prospect_facing_persona(
                persona, fallback_product=product_id or graph_cfg.site or ""
            ),
            speaker=meet_speaker,
            interactive=interactive_listen,
            listen=intake_listen,
            prefill=merged_prefill,
            will_share_screen=supports_screenshare,
        )
        print(f"[live] intake done: {intake.model_dump()}", flush=True)
        from navigator.meeting.intake import preferred_flow_id

        hint = preferred_flow_id(intake.looking_for)
        if hint:
            print(f"[live] intake suggests flow {hint!r} for looking_for", flush=True)

        # Screenshare tunnel after intake so voice is never blocked by probe failures.
        if supports_screenshare:
            print("[live] starting screenshare tunnel…", flush=True)
            try:
                tunnel = start_tunnel(
                    relay.port, binary=settings.tunnel_bin, ready_path=None
                )
                public_view = f"{tunnel.public_url}/view"
                print(f"[live] screenshare URL ready: {public_view}", flush=True)
                if tunnel._proc.poll() is not None:
                    raise RuntimeError("cloudflared died before screenshare")
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[live] WARN: screenshare tunnel failed: {exc} — continuing voice-only",
                    flush=True,
                )
                public_view = ""
        else:
            public_view = ""

        conversational = bool(settings.groq_api_key)

        # Inbound Meet audio for agent LISTENING (interrupt / anything-else).
        audio_frames = None
        if audio_bridge is not None:
            # Per-get wait: keep listening between walkthrough steps for interrupts.
            audio_frames = client.audio_stream(bot.id, timeout_s=6.0)
            print("[live] Meet audio STT armed", flush=True)

        session_id = session_id or uuid4()
        with ActionLog(settings.db_path) as log, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not headful)
            context = browser.new_context(viewport={"width": 1280, "height": 720})
            page = context.new_page()
            install_cursor(page)

            def _do_login(*, url: str, email: str, password: str, **_kw) -> None:
                login_product(page, url=url, email=email, password=password)

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
            # Prefer per-product vault; fall back to legacy process-wide env for
            # CLI smoke / single-tenant local runs.
            login_email = ""
            login_password = ""
            login_url = ""
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
                except VaultNotConfigured:
                    print(
                        "[live] credential vault not configured — "
                        "falling back to NAVIGATOR_PRODUCT_LOGIN_*",
                        flush=True,
                    )
            if not (login_email and login_password):
                login_email = settings.product_login_email
                login_password = settings.product_login_password
            if login_email and login_password:
                if not login_url:
                    login_url = settings.product_url.strip() or origin
                if "fixtures" in login_url or login_url.endswith(".html"):
                    login_url = origin
                gate = run_login_gate(
                    login_fn=_do_login,
                    url=login_url,
                    email=login_email,
                    password=login_password,
                    speaker=meet_speaker,
                    attendee=client,
                    bot_id=bot.id,
                )
                if gate is LoginGateResult.failed:
                    print(
                        "[live] login gate failed — aborting before Planning",
                        flush=True,
                    )
                    context.close()
                    browser.close()
                    return bot_id or ""
            else:
                page.goto(origin, wait_until="domcontentloaded")

            # Hold on the walkthrough start page (do not advance the flow yet).
            start_spec = graph_cfg.page(page_id)
            hold_url = urljoin(origin, start_spec.url.lstrip("/"))
            is_fixture = "fixtures" in hold_url or hold_url.endswith(".html")
            is_real_site = "fixtures" not in page.url and not page.url.endswith(".html")
            if is_fixture and is_real_site:
                print(f"[live] holding on logged-in real site: {page.url}", flush=True)
            elif hold_url.rstrip("/") not in page.url.rstrip("/"):
                print(f"[live] opening start page and holding: {hold_url}", flush=True)
                page.goto(hold_url, wait_until="domcontentloaded", timeout=60_000)
            else:
                print(f"[live] already on start page: {page.url}", flush=True)

            def _push() -> None:
                try:
                    push_frame(relay, page)
                except Exception as exc:  # noqa: BLE001
                    print(f"[live] frame push skipped: {exc}", flush=True)

            # Paint a few frames so /view is not blank when Attendee opens it.
            for _ in range(5):
                _push()
                time.sleep(0.15)

            baseline_hits = relay.frame_hits
            if public_view:
                print("[live] enabling screen share (holding start page)…", flush=True)
                try:
                    meet_speaker.say(
                        "One moment — I'm sharing my screen now. "
                        "I'll start the walkthrough once you can see it."
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[live] pre-share TTS skipped: {exc}", flush=True)

                arm_screenshare(client=client, bot_id=bot.id, public_view=public_view)
                live = wait_until_screenshare_live(
                    relay,
                    push_frame=_push,
                    baseline_frame_hits=baseline_hits,
                    min_frame_hits=10,
                    timeout_s=90.0,
                    settle_s=2.5,
                )
                if live:
                    try:
                        meet_speaker.say(
                            "Screen share is up. Let's walk through the product."
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"[live] share-ready TTS skipped: {exc}", flush=True)
                else:
                    try:
                        meet_speaker.say(
                            "Screen share is still catching up — I'll start the "
                            "walkthrough; tell me if you can't see my screen."
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"[live] share-timeout TTS skipped: {exc}", flush=True)
            else:
                print("[live] no screenshare URL — continuing voice-only", flush=True)
                try:
                    meet_speaker.say(
                        "I'll walk you through the product on this call. "
                        "Tell me if you can't follow along."
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[live] voice-only TTS skipped: {exc}", flush=True)

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
                if audio_frames is not None:
                    from navigator.agent.nodes.listening import _from_audio

                    try:
                        return (
                            _from_audio(
                                SimpleNamespace(
                                    audio_frames=audio_frames,
                                    transcribe_audio=None,
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

            deps = CallDeps(
                graph=graph_cfg,
                page=page,
                log=log,
                speaker=meet_speaker,
                scripted_flow=None if conversational else (page_id, flow_id),
                product_id=product_id or graph_cfg.site or "default",
                archive_dir=Path("archives"),
                groq_api_key=settings.groq_api_key or None,
                meeting_url=None,
                attendee=client,
                bot_id=bot.id,
                voice_agent_url=public_agent,
                push_frame=_push,
                interactive_listen=interactive_listen,
                audio_frames=audio_frames,
                intake=intake,
                is_bot_echo=lambda t: _is_likely_echo(t, meet_speaker.last_spoken),
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
            )

            mode = "conversational (LLM flow / handoff)" if conversational else f"scripted {page_id}/{flow_id}"
            print(f"[live] running demo graph ({mode})", flush=True)
            _check_stop(stop_event)
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
            failures = len(final.get("failures") or [])
            print(
                f"[live] demo finished: actions={len(final.get('entries') or [])} "
                f"failures={failures}",
                flush=True,
            )
            # Let final Meet TTS finish playing before we tear the bot down.
            time.sleep(1.5)

            context.close()
            browser.close()
    finally:
        if bot_id is not None:
            try:
                print(f"[live] leaving Meet (bot {bot_id})", flush=True)
                client.leave(bot_id)
            except Exception as exc:  # noqa: BLE001
                print(f"[live] leave failed: {exc}", flush=True)
        if audio_tunnel is not None:
            audio_tunnel.stop()
        if audio_bridge is not None:
            audio_bridge.stop()
        if tunnel is not None:
            tunnel.stop()
        relay.stop()

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
