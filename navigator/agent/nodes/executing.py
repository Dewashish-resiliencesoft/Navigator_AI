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


def _nudge_working(deps: CallDeps) -> None:
    """Keep the Live call alive while Playwright works."""
    lang = getattr(deps, "spoken_language", "en") or "en"
    if lang not in ("en", "hi"):
        lang = "en"
    maybe_nudge_live(deps.live_agent, language=lang)  # type: ignore[arg-type]


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
    label = state.get("nav_click_label")
    if label:
        from navigator.automation.browser.nav_click import click_nav_label
        from navigator.core.schemas import ToolResult

        _nudge_working(deps)
        try:
            click_nav_label(deps.page, label)
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

    ran_on = state["page_id"]
    speech_handle, narration_started_ns = _start_pre_action_speech(state, deps)
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
    if (
        _is_login_call(call)
        and _already_authenticated(deps.page)
        and not _target_visible(deps, ran_on, call)
    ):
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
            result, next_page_id = run_tool(
                deps.page, deps.graph, ran_on, call, on_frame=deps.push_frame
            )
        except Exception as exc:  # noqa: BLE001 — a missing element must never kill the demo
            result, next_page_id = _recover_failed_step(deps, ran_on, call, exc)
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
    if deps.push_frame is not None:
        deps.push_frame()

    _tell_live_where_we_are(deps, next_page_id, result)

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
            r"log\s*-?in|sign\s*-?in|signin|login|e-?mail|password|passwd|username|submit",
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
        pw = page.locator("input[type=password]")
        for i in range(min(pw.count(), 3)):
            if pw.nth(i).is_visible():
                return False
        return True
    except Exception:  # noqa: BLE001
        return False


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
        deps.page.locator(sel).first.wait_for(state="attached", timeout=wait_ms)
        return False
    except Exception as exc:  # noqa: BLE001
        if "Timeout" in str(exc):
            print(f"[exec] {raw!r} not on page after {wait_ms}ms — fallback", flush=True)
            return True
        return False
