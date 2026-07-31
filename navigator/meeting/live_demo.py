"""Live Meet demo: join quietly → intake → screenshare → agent demo graph.

Order (what the prospect experiences):

  1. Bot joins Meet *without* screen share (resources reserved)
  2. Console prints Meet join link (Resend email parked until rewired)
  3. Wait until a human participant joins
  4. Greet + ask name, company, business, what they're looking for (Meet chat)
  5. Pitch the wrapped product from the site-graph persona
  6. Enable screenshare of Playwright, run intro→listen→plan→execute→verify
  7. Leave bot, tear down

Listening: STT when audio hub registered; else stdin when TTY; else scripted.
Planning: Groq picks named flow or handoff when NAVIGATOR_GROQ_API_KEY set.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright

from navigator.agent.graph import build_graph
from navigator.agent.state import CallDeps, initial_state
from navigator.browser.cursor import install_cursor
from navigator.config.site_graph import load_site_graph
from navigator.logs.store import ActionLog
from navigator.meeting.attendee import AttendeeClient
from navigator.meeting.intake import run_intake
from navigator.meeting.meet_speaker import MeetSpeaker
from navigator.meeting.relay import push_frame, start_relay
from navigator.meeting.tunnel import start_tunnel
from navigator.settings import settings
from navigator.voice.tts import PiperSpeaker, PrintSpeaker


def _require_live_settings() -> None:
    if "localhost" in settings.attendee_base_url:
        raise RuntimeError(
            "NAVIGATOR_ATTENDEE_BASE_URL still points at localhost; "
            "use https://app.attendee.dev/api/v1 (or your self-hosted host)"
        )
    missing = [
        name
        for name, val in [
            ("NAVIGATOR_ATTENDEE_API_KEY", settings.attendee_api_key),
            ("NAVIGATOR_MEETING_URL", settings.meeting_url),
        ]
        if not val
    ]
    if missing:
        raise RuntimeError(f"missing env for live Meet demo: {', '.join(missing)}")


def wait_until_joined(
    client: AttendeeClient, bot_id: str, *, timeout_s: float = 180.0
) -> None:
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        bot = client.get(bot_id)
        last = bot.raw_state or bot.state
        if bot.state == "joined":
            return
        if bot.state == "fatal_error":
            raise RuntimeError(f"Attendee bot fatal_error (last state={last})")
        time.sleep(2)
    raise TimeoutError(f"Attendee bot did not join within {timeout_s}s (last={last})")


def _speaker(*, mute: bool):
    if mute:
        return PrintSpeaker()
    speaker = PiperSpeaker(settings.piper_voice, settings.piper_data_dir)
    return speaker if speaker.available() else PrintSpeaker()


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


def _share_meet_link() -> None:
    # ponytail: console-only while Resend free-tier credits are scarce.
    # Rewire auto-email when user asks (NAVIGATOR_RESEND_API_KEY + notify_email).
    print(f"[live] Meet join link: {settings.meeting_url}", flush=True)


def run_live_meet_demo(
    *,
    page_id: str = "inbox",
    flow_id: str = "send_test_message",
    headful: bool = True,
    mute: bool = False,
    interactive_listen: bool | None = None,
    open_meet_in_browser: bool | None = None,
    wait_for_human: bool = True,
    human_join_timeout_s: float = 300.0,
) -> str:
    """Join Meet, qualify prospect, then share screen and run demo. Returns bot id."""
    _require_live_settings()

    if interactive_listen is None:
        interactive_listen = sys.stdin.isatty()
    if open_meet_in_browser is None:
        open_meet_in_browser = settings.open_meet_in_browser

    # Bot joins BEFORE notify so prospect opens Meet with agent already present.
    if open_meet_in_browser:
        import webbrowser

        print(f"[live] opening Meet on this machine: {settings.meeting_url}", flush=True)
        webbrowser.open(settings.meeting_url)

    client = AttendeeClient(settings.attendee_base_url, settings.attendee_api_key)
    _leave_stale_bots(client, settings.meeting_url)
    speaker = _speaker(mute=mute)
    graph_cfg = load_site_graph(settings.site_graph)
    persona = graph_cfg.effective_persona()

    relay = start_relay()
    tunnel = None
    audio_bridge = None
    audio_tunnel = None
    bot_id: str | None = None

    try:
        print("[live] starting tunnel (screenshare armed, not started yet)…", flush=True)
        tunnel = start_tunnel(relay.port, binary=settings.tunnel_bin)
        public_view = f"{tunnel.public_url}/view"
        print(f"[live] screenshare URL ready: {public_view}", flush=True)
        if tunnel._proc.poll() is not None:
            raise RuntimeError("cloudflared died before Meet join")

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

        print("[live] Attendee joining Meet (no screen share yet)…", flush=True)
        bot = client.join(
            settings.meeting_url,
            bot_name=persona.agent_name,
            reserve_voice_agent=True,
            audio_websocket_url=audio_ws_url,
            join_chat_message=(
                f"Hi — {persona.agent_name} here. I'll greet you when you join, "
                f"then we'll talk before I share the screen."
            ),
        )
        bot_id = bot.id
        if audio_bridge is not None:
            client.register_audio_hub(bot.id, audio_bridge.inbound)
        print(f"[live] bot {bot_id} created ({bot.raw_state or bot.state})", flush=True)
        wait_until_joined(client, bot.id)
        print("[live] bot in Meet — share link now…", flush=True)
        _share_meet_link()
        print("[live] waiting for a human participant…", flush=True)

        if wait_for_human:
            try:
                human = client.wait_for_human_join(
                    bot.id, timeout_s=human_join_timeout_s
                )
                print(f"[live] human joined: {human!r}", flush=True)
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

        print("[live] starting intake (greet → qualify → pitch)…", flush=True)
        intake = run_intake(
            client=client,
            bot_id=bot.id,
            persona=persona,
            speaker=speaker,
            interactive=interactive_listen,
        )
        print(f"[live] intake done: {intake.model_dump()}", flush=True)

        print("[live] enabling screen share now…", flush=True)
        client.enable_screenshare(bot.id, public_view)

        conversational = bool(settings.groq_api_key)
        meet_speaker: MeetSpeaker | PrintSpeaker | PiperSpeaker = MeetSpeaker(
            speaker,
            client,
            bot.id,
            synthesizer=speaker if hasattr(speaker, "synthesize_wav") else None,
            also_chat=True,
        )

        # Inbound Meet audio when bridge received anything / STT path preferred.
        audio_frames = None
        if audio_bridge is not None:
            # Short per-chunk wait so LISTENING can fall back to stdin quickly.
            audio_frames = client.audio_stream(bot.id, timeout_s=3.0)
            print("[live] Meet audio STT armed (stdin fallback if quiet)", flush=True)

        session_id = uuid4()
        with ActionLog(settings.db_path) as log, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not headful)
            context = browser.new_context(viewport={"width": 1280, "height": 720})
            page = context.new_page()
            install_cursor(page)
            page.goto(graph_cfg.url_for(page_id), wait_until="domcontentloaded")
            push_frame(relay, page)

            def _push() -> None:
                try:
                    push_frame(relay, page)
                except Exception as exc:  # noqa: BLE001
                    print(f"[live] frame push skipped: {exc}", flush=True)

            deps = CallDeps(
                graph=graph_cfg,
                page=page,
                log=log,
                speaker=meet_speaker,
                scripted_flow=None if conversational else (page_id, flow_id),
                product_id="default",
                archive_dir=Path("archives"),
                groq_api_key=settings.groq_api_key or None,
                meeting_url=None,
                attendee=client,
                bot_id=bot.id,
                voice_agent_url=None,
                push_frame=_push,
                interactive_listen=interactive_listen,
                audio_frames=audio_frames,
            )

            mode = "conversational (LLM flow / handoff)" if conversational else f"scripted {page_id}/{flow_id}"
            print(f"[live] running demo graph ({mode})", flush=True)
            final = build_graph(deps).invoke(
                initial_state(session_id, page_id, max_turns=1)
            )
            _push()
            failures = len(final.get("failures") or [])
            print(
                f"[live] demo finished: actions={len(final.get('entries') or [])} "
                f"failures={failures}",
                flush=True,
            )

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


if __name__ == "__main__":
    print(run_live_meet_demo())
