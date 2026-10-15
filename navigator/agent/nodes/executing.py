"""EXECUTING: run one tool call from the plan.

One call per pass, not the whole plan, so every call gets its own VERIFYING pass
before the next one runs. A failed postcondition should stop the agent from
building on a broken assumption.
"""

from __future__ import annotations

import threading
import time

from navigator.agent.demo_trace import emit_demo_trace, emit_sync_trace
from navigator.agent.live_input import needs_live_input, resolve_demo_fill
from navigator.agent.state import CLEAR, CallDeps, CallState
from navigator.automation.browser.tools import execute as run_tool
from navigator.automation.external_links import (
    EXTERNAL_LINK_SPOKEN,
    is_external_url,
    revert_external_navigation,
)
from navigator.automation.login_match import VAULT_PASSWORD_SENTINEL
from navigator.core.schemas import FillField
from navigator.core.settings import settings
from navigator.voice.live_acks import maybe_nudge_live


def _start_pre_action_speech(state: CallState, deps: CallDeps):
    """Fire narration in the background; never block Playwright on prior TTS.

    Live serializes utterances inside ``say()`` (worker thread waits on the
    mouth). The demo thread must start ``run_tool`` immediately so navigation
    overlaps speech — waiting on ``pre_action_speech`` here caused
    speak-then-click. SPEAKING still joins the handle before post-plan lines.
    """
    lines = list(state.get("narration") or [])
    if not lines:
        return None, None

    from navigator.agent.speech_safety import prospect_safe_line
    from navigator.meeting.playback_handle import PlaybackHandle

    started_ns = time.monotonic_ns()
    from navigator.agent.utterance import item_text

    text = prospect_safe_line(item_text(lines[0]))
    if not text.strip():
        return None, None

    say_async = getattr(deps.speaker, "say_async", None)
    if say_async is not None:
        handle = say_async(text)
    else:
        handle = PlaybackHandle()

        def _say() -> None:
            try:
                deps.speaker.say(text)
            finally:
                handle._finish()

        handle._thread = threading.Thread(
            target=_say, name="pre-action-say", daemon=True
        )
        handle._thread.start()
    emit_demo_trace(
        deps.trace,
        session_id=state.get("session_id", ""),
        product_id=deps.product_id,
        event="narration_started",
        engine="gemini_live" if deps.live_agent is not None else "langgraph",
        step=int(state.get("executing_step") or state.get("walkthrough_step") or 0),
    )
    return handle, started_ns


_NUDGE_AFTER_S = 2.0


def _nudge_working(deps: CallDeps) -> None:
    """Filler ack ONLY if the action is still running after 2s (no spam)."""
    lang = getattr(deps, "spoken_language", "en") or "en"
    if lang not in ("en", "hi"):
        lang = "en"
    done = threading.Event()
    try:
        setattr(deps, "_nudge_done", done)
    except Exception:  # noqa: BLE001
        return

    def _fire() -> None:
        if not done.is_set():
            maybe_nudge_live(deps.live_agent, language=lang)  # type: ignore[arg-type]

    t = threading.Timer(_NUDGE_AFTER_S, _fire)
    t.daemon = True
    t.start()


def _end_nudge(deps: CallDeps) -> None:
    ev = getattr(deps, "_nudge_done", None)
    if ev is not None:
        ev.set()


def _tell_live_where_we_are(deps: CallDeps, page_id: str, result) -> None:
    """Keep the Live session aware of the screen without sending it video.

    Video input would cap the session at two minutes, so the model gets a short
    text note instead. This is what lets it answer "what am I looking at?".
    """
    live = deps.live_agent
    if live is None or not getattr(result, "ok", False):
        return
    if not hasattr(live, "add_context"):
        return
    try:
        name = deps.graph.page(page_id).name
    except Exception:  # noqa: BLE001
        return
    live.add_context(f"The screen now shows: {name}.")


def executing(state: CallState, deps: CallDeps) -> CallState:
    try:
        from navigator.meeting.ui_jobs import service_before_step

        service_before_step(deps)
    except Exception as exc:  # noqa: BLE001
        print(f"[flow] Q&A service failed: {exc}", flush=True)
    label = state.get("nav_click_label")
    if label:
        from navigator.automation.browser.nav_click import click_nav_label
        from navigator.core.schemas import ToolResult

        _nudge_working(deps)
        try:
            click_nav_label(deps.page, label)
            _end_nudge(deps)
            detail = f"clicked nav {label!r}"
            ok = True
        except Exception as exc:  # noqa: BLE001
            detail = str(exc).splitlines()[0] if str(exc) else "nav click failed"
            ok = False
        if deps.push_frame is not None:
            deps.push_frame()
        return CallState(
            nav_click_label=None,
            pending_calls=list(state.get("pending_calls") or []),
            last_call=None,
            last_result=ToolResult(
                ok=ok, tool="click_element", detail=detail, duration_ms=0
            ),
            last_page_id=state.get("page_id") or "",
        )

    pending = list(state["pending_calls"])
    if not pending:
        return CallState(last_call=None, last_result=None)

    call, rest = pending[0], pending[1:]
    from_vault = False
    if isinstance(call, FillField) and call.value == VAULT_PASSWORD_SENTINEL:
        pwd = deps.resolve_password() if deps.resolve_password else None
        if not pwd:
            from navigator.core.schemas import ToolResult

            return CallState(
                pending_calls=rest,
                last_call=call,
                last_result=ToolResult(
                    ok=False,
                    tool="fill_field",
                    detail="vault password unavailable",
                    duration_ms=0,
                ),
                last_page_id=state["page_id"],
                page_id=state["page_id"],
            )
        call = call.model_copy(update={"value": pwd})
        from_vault = True

    if isinstance(call, FillField) and not from_vault:
        if needs_live_input(call) or (call.value_ref or "").strip():
            call, live_detail = _resolve_user_fill(deps, call)
            _trace_live_input(deps, state, call, live_detail)

    # Strict sync: never act while the previous line is still playing (a
    # multi-call step left its line running while the next call navigated).
    prior = state.get("pre_action_speech")
    if prior is not None and hasattr(prior, "wait"):
        prior.wait(timeout=120.0)
    _return_from_detour(deps, call)
    _restore_step_screen(deps, call)
    ran_on = state["page_id"]
    # Speak-then-act: the part of the line about the page we are LEAVING
    # ("this is the landing page, let's click Login") is spoken before the click.
    pre_text, post_items = _split_speak_then_act(state, deps, call)
    if pre_text:
        _speak_blocking(deps, pre_text)
        state = dict(state)
        state["narration"] = post_items
    # Narrate AFTER the action and a fresh frame (sync: words match the screen).
    speech_handle, narration_started_ns = None, None
    # Frames pushed *during* the action, not just after it: the screenshare is a
    # JPEG poll, so cursor motion is only visible if we push through the move.
    _nudge_working(deps)
    action_started_ns = time.monotonic_ns()
    if narration_started_ns is not None:
        emit_sync_trace(
            deps.trace,
            session_id=state.get("session_id", ""),
            product_id=deps.product_id,
            engine="gemini_live" if deps.live_agent is not None else "langgraph",
            flow_id=state.get("walkthrough_flow_id", "") or "",
            step=int(state.get("executing_step") or state.get("walkthrough_step") or 0),
            narration_started_ns=narration_started_ns,
            action_started_ns=action_started_ns,
        )
    skip_narration = False
    if _is_login_call(call) and LOGIN_CREDS and not isinstance(call, FillField) \
            and _visible_password(deps.page):
        # The flow walks the real login: sign in visibly (password masked).
        result, next_page_id = _visible_login(deps, call, ran_on)
    elif (
        _is_login_call(call)
        and _already_authenticated(deps.page)
    ):
        skip_narration = True
        # Signed in already: any login/sign-in step is pointless (sign_in burned
        # 30s on the dashboard in the last run).
        from navigator.core.schemas import ToolResult

        print(f"[exec] skip login step {getattr(call, 'selector', '')!r}: already signed in ({deps.page.url})", flush=True)
        result, next_page_id = (
            ToolResult(ok=True, tool=getattr(call, "tool", "click_element"),
                       detail="skipped: already signed in", duration_ms=0),
            ran_on,
        )
    elif _missing_fast(deps, ran_on, call):
        result, next_page_id = _recover_failed_step(
            deps, ran_on, call, RuntimeError("element not on page (3s check)")
        )
    else:
        try:
            _st0 = time.monotonic()
            result, next_page_id = run_tool(
                deps.page, deps.graph, ran_on, call, on_frame=deps.push_frame
            )
            from navigator.core import logtime as _lt

            _lt.record("step_action", (time.monotonic() - _st0) * 1000, str(getattr(call, "selector", "")))
        except Exception as exc:  # noqa: BLE001 — a missing element must never kill the demo
            result, next_page_id = _recover_failed_step(deps, ran_on, call, exc)
    _end_nudge(deps)
    if is_external_url(deps.page.url, deps.graph.base_url):
        revert_external_navigation(deps.page, product_base=deps.graph.base_url)
        try:
            say_async = getattr(deps.speaker, "say_async", None)
            if say_async is not None:
                say_async(EXTERNAL_LINK_SPOKEN)
            else:
                deps.speaker.say(EXTERNAL_LINK_SPOKEN)
        except Exception as exc:  # noqa: BLE001
            print(f"[demo] external link disclaimer failed: {exc}", flush=True)
        result = result.model_copy(
            update={
                "ok": False,
                "detail": "external link skipped — not part of demo",
            }
        )
        next_page_id = ran_on
    if from_vault:
        result = result.model_copy(
            update={"detail": f"filled {call.selector} from vault"}
        )
    _verify_step_screen(deps, call)
    _sync_screen_to_narration(state, deps, call)
    if deps.push_frame is not None:
        deps.push_frame()
        try:
            from navigator.meeting.relay import wait_frame_sent

            # Narration starts only once a frame showing this screen went out.
            if not wait_frame_sent(0.7):
                time.sleep(0.12)
        except Exception:  # noqa: BLE001
            time.sleep(0.12)
    if skip_narration:
        # A login step we did not perform must not be narrated ("enter email and
        # password" over the dashboard was spoken twice in the 17:30 run).
        print("[exec] login step skipped — its narration is skipped too", flush=True)
        speech_handle = None
    else:
        speech_handle, _ = _start_pre_action_speech(state, deps)
        _component_tour(state, deps, call, speech_handle)

    _tell_live_where_we_are(deps, next_page_id, result)
    _note_step_screen(deps)

    return CallState(
        narration=CLEAR,
        pre_action_speech=speech_handle,
        pending_calls=rest,
        last_call=call.model_copy(update={"value": VAULT_PASSWORD_SENTINEL})
        if from_vault and isinstance(call, FillField)
        else call,
        last_result=result,
        last_page_id=ran_on,
        page_id=next_page_id,
    )


def _resolve_user_fill(deps: CallDeps, call: FillField) -> tuple[FillField, str]:
    def speak(line: str) -> None:
        try:
            deps.speaker.say(line)
        except Exception as exc:  # noqa: BLE001
            print(f"[live_input] TTS failed: {exc}", flush=True)

    if deps.live_answers is None:
        deps.live_answers = {}
    return resolve_demo_fill(
        call,
        live_answers=deps.live_answers,
        listen_once=deps.listen_once,
        extract_entity=deps.extract_entity,
        speak=speak,
    )


def _trace_live_input(
    deps: CallDeps, state: CallState, call: FillField, detail: str
) -> None:
    from navigator.logs.decisions import DecisionTraceStore

    try:
        store = DecisionTraceStore(deps.decision_db_path or settings.db_path)
        try:
            store.record(
                product_id=deps.product_id,
                session_id=state.get("session_id", ""),
                utterance="",
                branch="live_input",
                spoken=call.live_question or "",
                chosen_flow_id=None,
                detail=detail,
            )
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[trace] live_input skipped: {exc}", flush=True)


_LOGIN_RE = None


def _is_login_call(call) -> bool:
    import re

    global _LOGIN_RE
    if _LOGIN_RE is None:
        _LOGIN_RE = re.compile(
            r"log[\s_-]*in|sign[\s_-]*in|signin|login|e-?mail|password|passwd|username|submit",
            re.I,
        )
    blob = " ".join(
        str(getattr(call, k, "") or "") for k in ("selector", "text", "label", "value_ref")
    )
    return bool(_LOGIN_RE.search(blob))


def _already_authenticated(page) -> bool:
    """Signed in = not on an auth URL and no visible password field."""
    try:
        url = (page.url or "").lower()
        if any(k in url for k in ("login", "signin", "sign-in", "sign_in", "/auth")):
            return False
        if _visible_login_entry(page):
            return False  # public landing page with a "Log in" button: signed out
        pw = page.locator("input[type=password]")
        for i in range(min(pw.count(), 3)):
            if pw.nth(i).is_visible():
                return False
        return True
    except Exception:  # noqa: BLE001
        return False


#: (email, password) when the demo flow itself walks landing -> login. Set by
#: live_demo per demo; None means sign-in already happened before the flow.
LOGIN_CREDS: tuple[str, str] | None = None

_LOGIN_ENTRY_JS = r"""() => Array.from(document.querySelectorAll('a,button,[role=button]')).some(e => {
  const t = (e.innerText || '').trim().toLowerCase().replace(/\s+/g, ' ');
  const r = e.getBoundingClientRect();
  return r.width > 2 && r.height > 2 && /^(log ?in|sign ?in|login)$/.test(t);
})"""


def _visible_login_entry(page) -> bool:
    try:
        return bool(page.evaluate(_LOGIN_ENTRY_JS))
    except Exception:  # noqa: BLE001
        return False


def _visible_password(page) -> bool:
    try:
        pw = page.locator("input[type=password]")
        return any(pw.nth(i).is_visible() for i in range(min(pw.count(), 3)))
    except Exception:  # noqa: BLE001
        return False


def _visible_login(deps, call, ran_on):
    """Fill the login form on screen: email typed visibly, password masked, submit."""
    from navigator.core.schemas import ToolResult

    page = deps.page
    email, password = LOGIN_CREDS or ("", "")
    t0 = time.monotonic()
    try:
        user = page.locator(
            "input[type=email]:visible, input[name*=email i]:visible, input[name*=user i]:visible, "
            "input[autocomplete=username]:visible, input[type=text]:visible").first
        user.click(timeout=4000)
        user.fill("")
        user.press_sequentially(email, delay=45)
        if deps.push_frame is not None:
            deps.push_frame()
        pw = page.locator("input[type=password]:visible").first
        pw.click(timeout=4000)
        pw.fill("")
        pw.press_sequentially(password, delay=35)  # type=password: shown as dots
        if deps.push_frame is not None:
            deps.push_frame()
        btn = page.locator(
            "button[type=submit]:visible, button:visible:text-matches('^\\s*(sign ?in|log ?in|login|continue|submit)\\s*$', 'i'), "
            "input[type=submit]:visible").first
        try:
            btn.click(timeout=4000)
        except Exception:  # noqa: BLE001
            pw.press("Enter")
        for _ in range(40):
            page.wait_for_timeout(250)
            if not _visible_password(page):
                break
        ok = not _visible_password(page)
        print(f"[exec] visible login {'ok' if ok else 'still on login form'} -> {page.url}", flush=True)
        detail = "signed in on screen" if ok else "login form still shown after submit"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"visible login failed: {str(exc).splitlines()[0]}"
        print(f"[exec] {detail}", flush=True)
    return ToolResult(ok=ok, tool=getattr(call, "tool", "click_element"), detail=detail,
                      duration_ms=int((time.monotonic() - t0) * 1000)), ran_on


def _text_of(selector: str) -> str:
    sel = (selector or "").strip()
    for pre in ("text=", "text:"):
        if sel.startswith(pre):
            return sel[len(pre):].strip().strip("'\"")
    return sel if sel and not any(c in sel for c in "#.[]>=:/") else ""


def _recover_failed_step(deps, ran_on, call, exc):
    """Missing/hidden element: try text variants + scroll; else skip, never raise."""
    from navigator.core.schemas import ToolResult

    tool = getattr(call, "tool", "click_element")
    first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    print(f"[exec] step failed {getattr(call, 'selector', '')!r}: {first}", flush=True)
    if _is_login_call(call) and _already_authenticated(deps.page):
        print("[exec] login step skipped: already signed in", flush=True)
        return ToolResult(ok=True, tool=tool, detail="skipped: already signed in", duration_ms=0), ran_on
    page = deps.page
    raw = str(getattr(call, "selector", "") or "")
    try:
        el = (deps.graph.pages.get(ran_on).elements or {}).get(raw)  # alias → real selector
        raw = str(getattr(el, "selector", "") or el or raw) if el else raw
    except Exception:  # noqa: BLE001
        pass
    text = _text_of(raw) or _text_of(str(getattr(call, "selector", "")).replace("_", " "))
    candidates = []
    if raw and not raw.startswith("text="):
        candidates.append(lambda: page.locator(raw))
    if text:
        candidates += [
            lambda: page.get_by_role("button", name=text, exact=False),
            lambda: page.get_by_role("link", name=text, exact=False),
            lambda: page.get_by_text(text, exact=False),
            lambda: page.get_by_text(text.title(), exact=False),
        ]
    if tool == "click_element":
        for make in candidates:
            try:
                loc = make()
                n = min(loc.count(), 5)
                for i in range(n):
                    item = loc.nth(i)
                    try:
                        item.scroll_into_view_if_needed(timeout=1500)
                    except Exception:  # noqa: BLE001
                        pass
                    if item.is_visible():
                        item.click(timeout=3000)
                        if deps.push_frame is not None:
                            deps.push_frame()
                        print(f"[exec] recovered via fallback for {text or raw!r}", flush=True)
                        return ToolResult(ok=True, tool=tool, detail=f"clicked {text or raw!r} (fallback)", duration_ms=0), ran_on
            except Exception:  # noqa: BLE001
                continue
    try:
        bridge = "That part isn't showing on screen right now, so let's move on."
        say_async = getattr(deps.speaker, "say_async", None)
        (say_async or deps.speaker.say)(bridge)
    except Exception:  # noqa: BLE001
        pass
    return ToolResult(ok=True, tool=tool, detail=f"skipped: {first}", duration_ms=0), ran_on


def _target_visible(deps, page_id, call) -> bool:
    """True when the step's element is actually on screen (then never skip it)."""
    raw = str(getattr(call, "selector", "") or "")
    try:
        el = (deps.graph.pages.get(page_id).elements or {}).get(raw)
        sel = str(getattr(el, "selector", "") or el or raw) if el else raw
        loc = deps.page.locator(sel)
        for i in range(min(loc.count(), 3)):
            if loc.nth(i).is_visible():
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _missing_fast(deps, page_id, call, wait_ms: int = 3000) -> bool:
    """True when a click/fill target is absent after a short wait.

    Without this every missing recorded element cost a full 15s Playwright
    timeout (the "slow, heavy" demo), then failed anyway.
    """
    if getattr(call, "tool", "") not in {"click_element", "fill_field"}:
        return False
    raw = str(getattr(call, "selector", "") or "")
    if not raw:
        return False
    try:
        el = (deps.graph.pages.get(page_id).elements or {}).get(raw)
        sel = str(getattr(el, "selector", "") or el or raw) if el else raw
        loc = deps.page.locator(sel)
        deadline = time.monotonic() + wait_ms / 1000.0
        while True:
            # Attached-but-hidden (duplicate hidden inputs, closed modal) used to
            # pass this check and then burn 2x15s in the click: require visible.
            for i in range(min(loc.count(), 5)):
                if loc.nth(i).is_visible():
                    return False
            if time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        print(f"[exec] {raw!r} not visible after {wait_ms}ms — fallback", flush=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def _sync_screen_to_narration(state: CallState, deps: CallDeps, call) -> None:
    """Show what the line is about before it is spoken.

    Authoring time (script_sync.align_flow) already turned mismatched steps
    into navigate steps and cached a highlight per step in
    ``_meta.step_highlights``; here we only outline that control (no click,
    ~50ms). Runtime fallback for flows never aligned: a narration-only step
    whose line names a visible nav item opens it.
    """
    page = getattr(deps, "page", None)
    if page is None:
        return
    flow_id = str(state.get("walkthrough_flow_id") or "")
    step = state.get("executing_step")
    meta = getattr(getattr(deps, "graph", None), "meta", None) or {}
    try:
        from navigator.meeting.ui_jobs import highlight_text, show_feature

        hl = ((meta.get("step_highlights") or {}).get(flow_id) or {}).get(str(step), "")
        if hl:
            ok = highlight_text(page, hl)
            print(f"[sync] step {step}: highlight {hl!r} -> {'shown' if ok else 'not on screen'}", flush=True)
            return
        aligned = flow_id in (meta.get("step_sync") or {})
        spoken = str(getattr(call, "spoken", "") or "")
        if aligned or not spoken or getattr(call, "tool", "") != "wait_for" or getattr(call, "selector", "") != "body":
            return
        from navigator.knowledge.script_sync import heuristic_target

        controls = [{"label": c, "href": ""} for c in visible_controls_safe(page)]
        tgt = heuristic_target(spoken, controls)
        if tgt:
            res = show_feature(page, tgt["label"], None)
            print(f"[sync] step {step}: runtime fallback opened {tgt['label']!r} -> {res.get('ok')}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[sync] step {step}: skipped ({exc})", flush=True)


_CHARS_PER_S = 15.0  # Gemini Live narration pace (~150 wpm)
_FIRST_AUDIO_S = 1.3


def _return_from_detour(deps: CallDeps, call) -> None:
    """A prospect's request (show_feature/do_task) moved the screen: go back to
    where the scripted flow was before running its next step. A navigate step
    goes where it needs anyway."""
    try:
        from navigator.meeting import task_runner, ui_jobs

        if not ui_jobs.DETOUR_FROM:
            return
        back = ui_jobs.DETOUR_FROM.pop()
        ui_jobs.DETOUR_FROM.clear()
        ui_jobs.DEFERRED_TOURS.clear()
        task_runner.PENDING.clear()
        page = deps.page
        if page is None or getattr(call, "tool", "") == "navigate" or not back:
            return
        try:
            page.keyboard.press("Escape")  # close a form the detour left open
        except Exception:  # noqa: BLE001
            pass
        if (page.url or "") != back:
            page.goto(back, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(500)
        print(f"[exec] resumed scripted flow at {back}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[exec] detour return skipped: {exc}", flush=True)


def _tour_for_step(state: CallState, deps: CallDeps, call) -> list[dict]:
    meta = getattr(getattr(deps, "graph", None), "meta", None) or {}
    flow_id = str(state.get("walkthrough_flow_id") or "")
    step = str(state.get("executing_step"))
    tour = ((meta.get("step_tour") or {}).get(flow_id) or {}).get(step)
    if tour is not None:
        return list(tour)
    facts_all = meta.get("screen_facts") or {}
    if not facts_all:
        return []
    # Not precomputed: match the line against this screen's cached facts (string ops only).
    try:
        from urllib.parse import urlparse

        from navigator.knowledge.narration_expand import tour_targets

        path = urlparse(deps.page.url or "").path or "/"
        facts = facts_all.get(path) or facts_all.get(path.rstrip("/") + "/")
        lines = list(state.get("narration") or [])
        from navigator.agent.utterance import item_text

        text = item_text(lines[0]) if lines else str(getattr(call, "spoken", "") or "")
        return tour_targets(text, facts) if facts else []
    except Exception:  # noqa: BLE001
        return []


def _component_tour(state: CallState, deps: CallDeps, call, handle) -> None:
    """Outline each screen component as the line names it (hover/outline only,
    never a click). Targets are precomputed in ``_meta.step_tour``; timing
    follows Live's transcript of what was actually said, else the speech pace.
    Runs on the demo thread and keeps pumping Live tool jobs while it waits."""
    page = getattr(deps, "page", None)
    if page is None or handle is None:
        return
    tour = _tour_for_step(state, deps, call)
    if not tour:
        return
    from navigator.meeting.ui_jobs import highlight_text

    live = getattr(deps, "live_agent", None)
    t0 = time.monotonic()
    shown = []
    for item in tour:
        label = str(item.get("label") or "")
        due = t0 + _FIRST_AUDIO_S + float(item.get("at") or 0) / _CHARS_PER_S
        while time.monotonic() < due and not handle._done.is_set():
            said = " ".join(getattr(live, "_turn_said", []) or []).lower() if live is not None else ""
            if said and label.lower() in said:
                break
            handle.wait(0.1)
        if handle._done.is_set() and len(shown) >= 1:
            break
        if highlight_text(page, label, timeout_ms=600):
            shown.append(label)
            if deps.push_frame is not None:
                try:
                    deps.push_frame()
                except Exception:  # noqa: BLE001
                    pass
    print(f"[tour] step {state.get('executing_step')}: {len(shown)}/{len(tour)} components {shown}", flush=True)


def visible_controls_safe(page) -> list[str]:
    try:
        from navigator.meeting.ui_jobs import visible_controls

        return visible_controls(page)
    except Exception:  # noqa: BLE001
        return []


# ---- strict screen/voice sync helpers -------------------------------------
import re as _re

#: URL the scripted flow last left the screen on (after its step + frame).
STEP_URL: str = ""
_ACT_VERB = _re.compile(r"\b(click|tap|press|choose|select|open|sign in|log in|login)\b", _re.I)
_NEW_SCREEN = _re.compile(r"^(now|here|this is|next|finally|on the|we are|in the|below|at the top)\b", _re.I)


def _sentences(text: str) -> list[str]:
    parts = _re.split(r"(?<=[.!?।])\s+", (text or "").strip())
    return [p for p in parts if p.strip()]


def _target_words(state, deps, call) -> list[str]:
    words = []
    sel = str(getattr(call, "selector", "") or "")
    if sel and not any(ch in sel for ch in "#.[]=>:/"):
        words.append(sel.replace("_", " ").replace("-", " ").strip())
    meta = getattr(getattr(deps, "graph", None), "meta", None) or {}
    flow_id = str(state.get("walkthrough_flow_id") or "")
    hl = ((meta.get("step_highlights") or {}).get(flow_id) or {}).get(str(state.get("executing_step")), "")
    if hl:
        words.append(str(hl))
    return [w for w in words if len(w) >= 3]


def split_speak_then_act(text: str, tool: str, targets: list[str]) -> tuple[str, str]:
    """(before, after) for a step's line. Click-type steps speak the sentences
    up to the one naming the clicked control (plus follow-ups that still talk
    about the current page) BEFORE acting; navigate steps act first."""
    if tool == "navigate" or not targets:
        return "", text
    sents = _sentences(text)
    hit = -1
    for i, sn in enumerate(sents):
        low = sn.lower()
        if _ACT_VERB.search(sn) and any(t.lower() in low for t in targets):
            hit = i
            break
    if hit < 0:
        return "", text
    end = hit + 1
    while end < len(sents) and not _NEW_SCREEN.match(sents[end].strip()):
        end += 1
    return " ".join(sents[:end]), " ".join(sents[end:])


def _split_speak_then_act(state, deps, call):
    lines = list(state.get("narration") or [])
    if not lines:
        return "", lines
    if _is_login_call(call) and not (LOGIN_CREDS and _visible_password(deps.page)) \
            and _already_authenticated(deps.page):
        return "", lines  # step will be skipped silently
    try:
        from navigator.agent.utterance import item_text

        text = item_text(lines[0])
        pre, post = split_speak_then_act(text, str(getattr(call, "tool", "")), _target_words(state, deps, call))
        if not pre:
            return "", lines
        print(f"[sync] speak-then-act: {pre[:70]!r} before {getattr(call, 'tool', '')}", flush=True)
        return pre, ([post] + lines[1:]) if post else lines[1:]
    except Exception as exc:  # noqa: BLE001
        print(f"[sync] split skipped: {exc}", flush=True)
        return "", lines


def _speak_blocking(deps, text: str) -> None:
    from navigator.agent.speech_safety import prospect_safe_line

    text = prospect_safe_line(text)
    if not text.strip():
        return
    if deps.push_frame is not None:
        deps.push_frame()
    try:
        deps.speaker.say(text)
    except Exception as exc:  # noqa: BLE001
        print(f"[sync] pre-action line failed: {exc}", flush=True)


def _note_step_screen(deps) -> None:
    global STEP_URL
    try:
        STEP_URL = deps.page.url or ""
    except Exception:  # noqa: BLE001
        pass


def _restore_step_screen(deps, call) -> None:
    """Q&A/tool detours that left no DETOUR_FROM mark still moved the screen:
    put the flow's last screen back before an in-page step speaks about it."""
    page = getattr(deps, "page", None)
    if page is None or not STEP_URL or getattr(call, "tool", "") == "navigate":
        return
    try:
        cur = page.url or ""
        if cur.split("#")[0].rstrip("/") != STEP_URL.split("#")[0].rstrip("/"):
            print(f"[sync] screen drifted to {cur} — restoring {STEP_URL}", flush=True)
            page.goto(STEP_URL, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(400)
    except Exception as exc:  # noqa: BLE001
        print(f"[sync] restore skipped: {exc}", flush=True)


def expected_path(graph, call) -> str:
    if getattr(call, "tool", "") != "navigate":
        return ""
    try:
        pg = graph.pages.get(getattr(call, "page_id", "")) if graph is not None else None
        u = str(getattr(pg, "url", "") or "")
    except Exception:  # noqa: BLE001
        return ""
    if not u or any(ch in u for ch in "*{}<>"):
        return ""
    from urllib.parse import urlparse

    return (urlparse(u).path or "/").rstrip("/") or "/"


def _verify_step_screen(deps, call) -> None:
    """navigate -> verify the URL is the step's screen (retry once) before speaking."""
    want = expected_path(getattr(deps, "graph", None), call)
    page = getattr(deps, "page", None)
    if not want or page is None:
        return
    from urllib.parse import urljoin, urlparse

    for attempt in range(2):
        cur = (urlparse(page.url or "").path or "/").rstrip("/") or "/"
        if cur == want or cur.startswith(want + "/"):
            return
        print(f"[sync] step screen mismatch: on {cur}, expected {want} — re-navigating", flush=True)
        try:
            page.goto(urljoin(deps.graph.base_url, want if want == "/" else want + "/"), wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(500)
        except Exception as exc:  # noqa: BLE001
            print(f"[sync] re-navigate failed: {exc}", flush=True)
