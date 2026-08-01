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
from navigator.browser.login_gate import LoginGateResult, run_login_gate
from navigator.browser.product_login import login_product
from navigator.config.site_graph import load_site_graph
from navigator.logs.store import ActionLog
from navigator.meeting.attendee import AttendeeClient
from navigator.meeting.intake import run_intake
from navigator.meeting.meet_speaker import MeetSpeaker
from navigator.meeting.relay import push_frame, start_relay
from navigator.meeting.screenshare import arm_screenshare
from navigator.meeting.tunnel import start_tunnel
from navigator.settings import settings
from navigator.voice.tts import PiperSpeaker, PrintSpeaker


def assert_live_site_graph(path: Path) -> None:
    text = path.read_text()
    if "tests/fixtures" in text or "crm_dashboard.html" in text:
        raise RuntimeError(
            f"live demo refuses fixture site graph {path}. "
            "Record ResilioHub: python -m navigator.record --url $NAVIGATOR_PRODUCT_URL"
        )


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


def _require_piper_for_meet(*, mute: bool) -> PiperSpeaker | PrintSpeaker:
    """Live Meet needs Piper WAV → Attendee speak. Chat is not a substitute."""
    if mute:
        return PrintSpeaker()
    piper = PiperSpeaker(settings.piper_voice, settings.piper_data_dir)
    if piper.available():
        return piper
    raise RuntimeError(
        f"Piper voice {settings.piper_voice!r} missing under {settings.piper_data_dir}/ "
        f"— Meet has no audio without it. Install:\n"
        f"  .venv/bin/pip install 'piper-tts>=1.4'\n"
        f"  .venv/bin/python -m piper.download_voices {settings.piper_voice} "
        f"--data-dir {settings.piper_data_dir}"
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
    assert_live_site_graph(Path(settings.site_graph))
    _require_live_settings()

    if interactive_listen is None:
        interactive_listen = sys.stdin.isatty()
    # Host opens Meet first so they can admit the bot (or disable waiting room).
    if open_meet_in_browser is None:
        open_meet_in_browser = True

    client = AttendeeClient(settings.attendee_base_url, settings.attendee_api_key)
    _leave_stale_bots(client, settings.meeting_url)
    speaker = _require_piper_for_meet(mute=mute)
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

        # Host first → then bot. Avoids chicken-egg: bot stuck in waiting room
        # with nobody to admit them.
        _share_meet_link()
        print(
            "[live] Open that Meet link as HOST now.\n"
            "[live] Turn OFF waiting room (Meet → host settings) OR admit "
            f"{persona.agent_name} when asked.\n"
            "[live] Bot joins in a few seconds so you are already present…",
            flush=True,
        )
        if open_meet_in_browser:
            import webbrowser

            print(f"[live] opening Meet on this machine: {settings.meeting_url}", flush=True)
            webbrowser.open(settings.meeting_url)
        time.sleep(8)

        print("[live] Attendee joining Meet (no chat, voice only)…", flush=True)
        bot = client.join(
            settings.meeting_url,
            bot_name=persona.agent_name,
            reserve_voice_agent=True,
            audio_websocket_url=audio_ws_url,
            # No join_chat_message — voice only.
        )
        bot_id = bot.id
        if audio_bridge is not None:
            client.register_audio_hub(bot.id, audio_bridge.inbound)
        print(f"[live] bot {bot_id} created ({bot.raw_state or bot.state})", flush=True)
        wait_until_joined(client, bot.id)
        print("[live] bot in Meet", flush=True)

        meet_speaker: MeetSpeaker | PrintSpeaker | PiperSpeaker = MeetSpeaker(
            speaker,
            client,
            bot.id,
            synthesizer=speaker if hasattr(speaker, "synthesize_wav") else None,
            also_chat=False,
        )

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

        print("[live] starting intake (voice into Meet)…", flush=True)
        intake = run_intake(
            persona=persona,
            speaker=meet_speaker,
            interactive=interactive_listen,
        )
        print(f"[live] intake done: {intake.model_dump()}", flush=True)

        print("[live] enabling screen share now…", flush=True)
        arm_screenshare(client=client, bot_id=bot.id, public_view=public_view)

        conversational = bool(settings.groq_api_key)

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

            def _do_login(*, url: str, email: str, password: str, **_kw) -> None:
                login_product(page, url=url, email=email, password=password)

            if settings.product_login_email and settings.product_login_password:
                login_url = settings.product_url
                if not login_url:
                    raise RuntimeError(
                        "live demo needs NAVIGATOR_PRODUCT_URL (not fixture)"
                    )
                if "fixtures" in login_url or login_url.endswith(".html"):
                    raise RuntimeError(
                        "live demo needs NAVIGATOR_PRODUCT_URL (not fixture)"
                    )
                gate = run_login_gate(
                    login_fn=_do_login,
                    url=login_url,
                    email=settings.product_login_email,
                    password=settings.product_login_password,
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
                start = settings.product_url
                if not start:
                    raise RuntimeError(
                        "live demo needs NAVIGATOR_PRODUCT_URL (not fixture)"
                    )
                if "fixtures" in start or start.endswith(".html"):
                    raise RuntimeError(
                        "live demo needs NAVIGATOR_PRODUCT_URL (not fixture)"
                    )
                page.goto(start, wait_until="domcontentloaded")

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
                intake=intake,
            )

            mode = "conversational (LLM flow / handoff)" if conversational else f"scripted {page_id}/{flow_id}"
            print(f"[live] running demo graph ({mode})", flush=True)
            final = build_graph(deps).invoke(
                initial_state(
                    session_id,
                    page_id,
                    max_turns=settings.live_max_turns,
                    walkthrough_flow_id=settings.live_walkthrough_flow,
                )
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
    args = parser.parse_args()
    if args.login_only:
        raise SystemExit(run_login_only(headful=args.headful))
    print(run_live_meet_demo())
