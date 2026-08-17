"""Timeline playback for recorded narrated flows.

Replays a recording as a copy of itself: every cue sits at an absolute time on
the flow's clock, and playback waits until that time rather than sleeping
between steps. Narrated steps start speech and the cursor/click together so
Meet sees the pointer on the control while the line names it.

LangGraph speak-then-click walkthrough is the fallback when metadata is missing.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from navigator.agent.live_input import needs_live_input, resolve_live_fill
from navigator.agent.demo_trace import emit_demo_trace, emit_sync_trace
from navigator.agent.playback_schedule import build_schedule, fmt_ms
from navigator.agent.state import CallDeps
from navigator.automation.browser.tools import execute as run_tool
from navigator.automation.login_match import VAULT_PASSWORD_SENTINEL
from navigator.automation.narration import spoken_for_live_step
from navigator.core.schemas import ActionLogEntry, ClickElement, FillField, ToolResult
from navigator.knowledge.site_graph import SiteGraphError
from navigator.logs.store import utcnow
from navigator.meeting.playback_handle import PlaybackHandle

if TYPE_CHECKING:
    from navigator.meeting.meet_speaker import MeetSpeaker

PAUSE_LINE = "One moment — let me get that step right."
SKIP_LINE = "Moving on — we'll skip that step for now."
#: After a browser act, let Meet catch the frame before the next line.
_ACT_SETTLE_S = 0.25

DemoOrigin = Literal["dashboard_test", "public_embed"]

_TIMELINE_RETRIES = 3


@dataclass
class TimelineOutcome:
    entries: list[ActionLogEntry] = field(default_factory=list)
    failures: list[ActionLogEntry] = field(default_factory=list)
    paused: bool = False
    hard_fail: bool = False
    steps_run: int = 0


def _page_dead(exc: BaseException | str) -> bool:
    msg = str(exc).lower()
    return (
        "has been closed" in msg
        or "target closed" in msg
        or "browser has been closed" in msg
    )


def _stopped(deps: CallDeps) -> bool:
    ev = getattr(deps, "stop_event", None)
    return ev is not None and ev.is_set()


def _demo_origin(deps: CallDeps) -> DemoOrigin:
    origin = getattr(deps, "demo_origin", "dashboard_test")
    return origin if origin in ("dashboard_test", "public_embed") else "dashboard_test"


def _hard_stop_on_click_fail(deps: CallDeps, *, strict: bool) -> bool:
    return strict and _demo_origin(deps) == "public_embed"


def _wait_until(deps: CallDeps, deadline: float) -> None:
    """Wait for the monotonic ``deadline``, pumping screenshare frames meanwhile.

    Absolute deadline, not a duration — a cue that is already late fires at once
    and the caller shifts the rest of the schedule to match.
    """
    from navigator.voice.language import poll_barge_in_language_switch

    push = deps.push_frame
    # ~12 fps during waits — 60fps JPEG spam freezes Chromium mid-demo.
    slice_s = 0.08
    while True:
        poll_barge_in_language_switch(deps)
        if _stopped(deps):
            return
        if push is not None:
            try:
                push()
            except Exception as exc:  # noqa: BLE001
                if _page_dead(exc):
                    return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(slice_s, remaining))


def _flow_schedule(deps: CallDeps, flow_id: str, lines: list[str], n_steps: int):
    return build_schedule(
        n_steps=n_steps,
        clicks=deps.graph.flow_step_clicks(flow_id),
        speech=deps.graph.flow_step_speech(flow_id),
        lines=lines,
        timing=deps.graph.flow_step_timing(flow_id),
        tts_ms=lambda _text: None,
    )


def _start_speech(deps: CallDeps, line: str) -> PlaybackHandle | None:
    text = spoken_for_live_step(line)
    if not text.strip():
        return None
    live = getattr(deps, "live_agent", None)
    if live is not None and hasattr(live, "say"):
        # Background thread keeps same-step speak/act lead-in overlap.
        handle = PlaybackHandle()

        def _worker() -> None:
            try:
                live.say(text, mode="natural")  # type: ignore[call-arg]
            except Exception as exc:  # noqa: BLE001
                handle.error = str(exc)
                print(f"[timeline] live.say failed: {exc}", flush=True)
            finally:
                handle._finish()

        handle._thread = threading.Thread(
            target=_worker, name="live-say", daemon=True
        )
        handle._thread.start()
        return handle
    speaker = deps.speaker
    if hasattr(speaker, "say_async"):
        return speaker.say_async(text)  # type: ignore[union-attr]
    speaker.say(text)
    return None


def _wait_speech(handle: PlaybackHandle | None) -> None:
    """Finish prior line before starting the next — stops stacked/mismatched audio."""
    if handle is None:
        return
    try:
        handle.wait(timeout=120.0)
    except Exception:  # noqa: BLE001
        pass


def _flow_is_stub(calls: list) -> bool:
    """True when a playlist row has no real demo actions (empty / wait-only)."""
    if not calls:
        return True
    for call in calls:
        tool = getattr(call, "tool", None) or ""
        if tool in {"click_element", "fill_field", "navigate"}:
            return False
        if tool == "wait_for":
            sel = getattr(call, "selector", None) or ""
            if sel and sel != "body":
                return False
    return True


def _execute_call(
    deps: CallDeps,
    call,
    *,
    page_id: str,
    step_index: int | None = None,
    flow_id: str | None = None,
):
    from_vault = False
    if isinstance(call, FillField) and call.value == VAULT_PASSWORD_SENTINEL:
        pwd = deps.resolve_password() if deps.resolve_password else None
        if not pwd:
            return call, ToolResult(
                ok=False,
                tool="fill_field",
                detail="vault password unavailable",
                duration_ms=0,
            ), page_id
        call = call.model_copy(update={"value": pwd})
        from_vault = True

    if isinstance(call, FillField) and needs_live_input(call) and not from_vault:
        call, _detail = resolve_live_fill(deps, call)

    ran_on = page_id
    on_frame = deps.push_frame
    mouse_path: list[dict[str, int]] | None = None
    if step_index is not None and flow_id:
        mouse_path = deps.graph.flow_step_mouse_paths(flow_id).get(step_index)
    if on_frame is not None:
        on_frame()
    result, new_page = run_tool(
        deps.page,
        deps.graph,
        ran_on,
        call,
        on_frame=on_frame,
        mouse_path=mouse_path,
    )
    if on_frame is not None:
        on_frame()
    return call, result, new_page


def _scroll_retry(deps: CallDeps, call, *, page_id: str) -> None:
    if not hasattr(call, "selector"):
        return
    try:
        css = deps.graph.selector(page_id, call.selector)
    except Exception:  # noqa: BLE001
        return
    try:
        deps.page.locator(css).first.scroll_into_view_if_needed(timeout=3000)
    except Exception:  # noqa: BLE001
        pass


def _self_visible_click(call) -> bool:
    """Recorded clicks often expect the clicked alias still visible.

    After a real CTA the button leaves the DOM (nav/modal) → verify waits
    ~15s × retries and the UI shows FAIL even though the click worked.
    """
    if not isinstance(call, ClickElement):
        return False
    expects = call.expects
    return (
        expects.check == "visible"
        and expects.selector is not None
        and expects.selector == call.selector
    )


def _junk_playback_call(deps: CallDeps, call, *, page_id: str) -> str | None:
    """Why a saved step should be skipped at replay (recorder noise in old YAML)."""
    if not isinstance(call, ClickElement):
        return None
    alias = (call.selector or "").strip()
    try:
        css = deps.graph.selector(page_id, alias)
    except Exception:  # noqa: BLE001
        css = alias
    css_l = (css or "").strip().lower()
    # Bare CSS tag as selector (e.g. "img") — not a real control.
    if css_l in {"svg", "path", "div", "span", "button", "body", "html", "img"}:
        return f"bare tag {css_l!r}"
    if css_l.startswith("text="):
        label = css.split("=", 1)[-1].strip().strip("'\"")
        if label in {"on", "in", "or", "to", "of", "at", "by", "as", "is", "the"}:
            return f"particle text {css!r}"
    alias_l = alias.lower()
    # Common accidental heading / chrome taps from noisy recordings.
    if alias_l in {
        "welcome_back",
        "img_el",
        "support_agent",
        "business_owner",
        "on",
    }:
        return f"junk alias {alias!r}"
    return None


def run_flow_timeline(
    deps: CallDeps,
    *,
    session_id: UUID,
    page_id: str,
    flow_id: str,
    strict: bool = True,
) -> TimelineOutcome:
    """Replay one flow using relative inter-step pacing + parallel speech/click."""
    from navigator.automation.browser.cursor import set_playback_mode

    set_playback_mode(True)
    try:
        return _run_flow_timeline_inner(
            deps,
            session_id=session_id,
            page_id=page_id,
            flow_id=flow_id,
            strict=strict,
        )
    finally:
        set_playback_mode(False)


def _run_flow_timeline_inner(
    deps: CallDeps,
    *,
    session_id: UUID,
    page_id: str,
    flow_id: str,
    strict: bool = True,
) -> TimelineOutcome:
    """Replay one flow using relative inter-step pacing + parallel speech/click."""
    outcome = TimelineOutcome()
    try:
        calls = list(deps.graph.flow(page_id, flow_id))
    except SiteGraphError as exc:
        raise RuntimeError(
            f"timeline flow {flow_id!r} not found on page {page_id!r}"
        ) from exc
    if not calls:
        return outcome

    lines = deps.graph.flow_narration_lines(flow_id)
    while len(lines) < len(calls):
        lines.append("")

    cues, total_ms = _flow_schedule(deps, flow_id, lines, len(calls))
    print(
        f"[timeline] {flow_id!r}: {len(calls)} steps, {len(cues)} cues, "
        f"length {fmt_ms(total_ms)}",
        flush=True,
    )

    current_page = page_id
    speech: PlaybackHandle | None = None
    #: Step index of the line currently playing (async). Used so a later act
    #: cannot fire while an earlier sentence is still describing another screen
    #: — which is what happens when TTS prefetch misses and the schedule never
    #: stretches (see live log: act 1 at 00:56 while speak 0 still running).
    speech_idx: int | None = None
    narration_started: dict[int, int] = {}
    skipped: set[int] = set()
    last_was_act = False
    t0 = time.monotonic()
    #: Runtime slip (retries, slow pages) and TTS overrun both land here, so a
    #: late step moves narration AND action together instead of desyncing them.
    shift_ms = 0

    def elapsed_ms() -> int:
        return int((time.monotonic() - t0) * 1000)

    for cue in cues:
        if _stopped(deps):
            break
        if cue.idx in skipped:
            continue

        due_ms = cue.at_ms + shift_ms
        _wait_until(deps, t0 + due_ms / 1000.0)
        if _stopped(deps):
            break

        call = calls[cue.idx]
        # Checked at whichever cue for this step lands first, so a junk step
        # drops its narration too instead of talking about a skipped click.
        junk = _junk_playback_call(deps, call, page_id=current_page)
        if junk:
            print(
                f"[timeline] skip junk step {cue.idx} on {flow_id!r}: {junk}",
                flush=True,
            )
            skipped.add(cue.idx)
            continue

        if cue.kind == "speak":
            # Mid-demo "talk in Hindi" / settings already hi — apply before the line.
            from navigator.voice.language import poll_barge_in_language_switch

            # A switch clears the WAV cache, so the durations this schedule was
            # built from no longer hold. No rebuild needed: the guard below
            # absorbs the overrun, which makes this step's action late, which
            # shifts the rest — narration and action stay together.
            poll_barge_in_language_switch(deps)
            if last_was_act:
                _wait_until(deps, time.monotonic() + _ACT_SETTLE_S)
                last_was_act = False
                late = elapsed_ms() - due_ms
                if late > 0:
                    shift_ms += late
            # Guard only: the schedule is what keeps lines from overlapping.
            _wait_speech(speech)
            print(
                f"[timeline] {fmt_ms(elapsed_ms())} speak step {cue.idx}",
                flush=True,
            )
            narration_started[cue.idx] = time.monotonic_ns()
            emit_demo_trace(
                deps.trace,
                session_id=session_id,
                product_id=deps.product_id,
                event="narration_started",
                engine="timeline",
                flow_id=flow_id,
                step=cue.idx,
            )
            speech = _start_speech(deps, cue.text)
            speech_idx = cue.idx if speech is not None else None
            continue

        # Same-step lead-in still overlaps (speak N + act N). A *later* act must
        # not run under an earlier line — prefetch miss leaves the schedule too
        # short, and click-before-talk metadata puts act N+1 ahead of speak N+1.
        if speech is not None and speech_idx is not None and speech_idx < cue.idx:
            _wait_speech(speech)
            speech = None
            speech_idx = None

        action_started_ns = time.monotonic_ns()
        if cue.idx in narration_started:
            emit_sync_trace(
                deps.trace,
                session_id=session_id,
                product_id=deps.product_id,
                engine="timeline",
                flow_id=flow_id,
                step=cue.idx,
                narration_started_ns=narration_started[cue.idx],
                action_started_ns=action_started_ns,
            )
        print(f"[timeline] {fmt_ms(elapsed_ms())} act step {cue.idx}", flush=True)
        last_was_act = True
        entry, current_page = _run_step(
            deps,
            call,
            page_id=current_page,
            session_id=session_id,
            step_index=cue.idx,
            flow_id=flow_id,
        )
        # Retries and slow pages push the rest of the flow back rather than
        # letting later cues fire early to "catch up".
        late = elapsed_ms() - due_ms
        if late > 0:
            shift_ms += late

        outcome.entries.append(entry)
        outcome.steps_run += 1
        if not entry.failed:
            continue

        skipped.add(cue.idx)
        outcome.failures.append(entry)
        if not entry.actual_result.ok:
            if _hard_stop_on_click_fail(deps, strict=strict):
                _wait_speech(speech)
                deps.speaker.say(PAUSE_LINE)
                outcome.paused = True
                outcome.hard_fail = True
                return outcome
            print(
                f"[timeline] click miss step {cue.idx} on {flow_id!r}, continuing: "
                f"{entry.actual_result.detail}",
                flush=True,
            )
            if _demo_origin(deps) == "dashboard_test":
                deps.speaker.say(SKIP_LINE)
            continue

        print(
            f"[timeline] verify miss step {cue.idx} on {flow_id!r}, continuing: "
            f"{entry.verify.actual}",
            flush=True,
        )

    _wait_speech(speech)
    return outcome


def _run_step(
    deps: CallDeps,
    call,
    *,
    page_id: str,
    session_id: UUID,
    step_index: int | None = None,
    flow_id: str | None = None,
) -> tuple[ActionLogEntry, str]:
    """Execute one step with retries. Returns entry and active page_id."""
    from navigator.automation.browser.verify import check
    from navigator.core.schemas import VerifyResult

    last: ActionLogEntry | None = None
    current_page = page_id
    for attempt in range(_TIMELINE_RETRIES):
        call, result, current_page = _execute_call(
            deps,
            call,
            page_id=current_page,
            step_index=step_index,
            flow_id=flow_id,
        )
        if result.ok and _self_visible_click(call):
            # Click landed; do not re-assert the same control is still on screen.
            verdict = VerifyResult(passed=True, actual="click landed")
        elif result.ok:
            verdict = check(deps.page, deps.graph, current_page, call.expects)
        else:
            verdict = VerifyResult(
                passed=False, actual=f"action failed: {result.detail}"
            )
        entry = ActionLogEntry(
            session_id=session_id,
            product_id=deps.product_id,
            page=current_page,
            tool_call=call,
            expected_postcondition=call.expects,
            actual_result=result,
            verify=verdict,
            source=call.source if isinstance(call, FillField) else "agent",
            timestamp=utcnow(),
        )
        deps.log.append(entry)
        last = entry
        if result.ok and verdict.passed:
            return entry, current_page
        if _page_dead(result.detail) or _page_dead(verdict.actual):
            print(
                f"[timeline] page closed — aborting step retries: {result.detail}",
                flush=True,
            )
            return entry, current_page
        # Click ok but verify miss → do not re-click (re-tap wrong page state).
        if result.ok:
            return entry, current_page
        if attempt < _TIMELINE_RETRIES - 1 and not _stopped(deps):
            print(
                f"[timeline] step retry ({result.detail or verdict.actual})",
                flush=True,
            )
            _scroll_retry(deps, call, page_id=current_page)
            time.sleep(0.5 + attempt * 0.4)
    assert last is not None
    return last, current_page


def run_flow_strict(
    deps: CallDeps,
    *,
    session_id: UUID,
    page_id: str,
    flow_id: str,
    strict: bool = True,
) -> TimelineOutcome:
    """Execute flow YAML steps in order — no LLM, no timeline, no detours."""
    outcome = TimelineOutcome()
    try:
        calls = list(deps.graph.flow(page_id, flow_id))
    except SiteGraphError as exc:
        raise RuntimeError(
            f"strict flow {flow_id!r} not found on page {page_id!r}"
        ) from exc
    if not calls:
        return outcome

    lines = deps.graph.flow_narration_lines(flow_id)
    while len(lines) < len(calls):
        lines.append("")

    current_page = page_id
    for step, call in enumerate(calls):
        if _stopped(deps):
            break
        junk = _junk_playback_call(deps, call, page_id=current_page)
        if junk:
            print(
                f"[timeline] skip junk step {step} on {flow_id!r}: {junk}",
                flush=True,
            )
            continue
        line = lines[step] if step < len(lines) else ""
        narration_started_ns = time.monotonic_ns() if line.strip() else None
        if narration_started_ns is not None:
            emit_demo_trace(
                deps.trace,
                session_id=session_id,
                product_id=deps.product_id,
                event="narration_started",
                engine="strict_playlist",
                flow_id=flow_id,
                step=step,
            )
        _start_speech(deps, line)
        action_started_ns = time.monotonic_ns()
        if narration_started_ns is not None:
            emit_sync_trace(
                deps.trace,
                session_id=session_id,
                product_id=deps.product_id,
                engine="strict_playlist",
                flow_id=flow_id,
                step=step,
                narration_started_ns=narration_started_ns,
                action_started_ns=action_started_ns,
            )
        entry, current_page = _run_step(
            deps,
            call,
            page_id=current_page,
            session_id=session_id,
            step_index=step,
            flow_id=flow_id,
        )
        outcome.entries.append(entry)
        outcome.steps_run += 1
        if not entry.failed:
            continue
        outcome.failures.append(entry)
        if not entry.actual_result.ok:
            if _hard_stop_on_click_fail(deps, strict=strict):
                deps.speaker.say(PAUSE_LINE)
                outcome.paused = True
                outcome.hard_fail = True
                return outcome
            if _demo_origin(deps) == "dashboard_test":
                continue
            if strict:
                deps.speaker.say(PAUSE_LINE)
                outcome.paused = True
                outcome.hard_fail = True
                return outcome
    return outcome


def run_playlist_strict(
    deps: CallDeps,
    *,
    session_id: UUID,
    auto_play: bool = True,
    strict: bool = True,
) -> dict:
    """Run every demo_playlist flow as exact YAML steps (no explore/adapt)."""
    graph = deps.graph
    playlist = sorted(graph.demo_playlist, key=lambda x: x.order) if graph.demo_playlist else []
    all_entries: list[ActionLogEntry] = []
    all_failures: list[ActionLogEntry] = []
    paused = False

    for item in playlist:
        if _stopped(deps):
            break
        try:
            calls = list(graph.flow(item.page_id, item.flow_id))
        except SiteGraphError:
            calls = []
        if _flow_is_stub(calls):
            print(
                f"[strict] skip empty/stub flow {item.page_id}/{item.flow_id} "
                f"({item.name or item.flow_id}) — re-record with real clicks",
                flush=True,
            )
            continue
        print(
            f"[strict] flow {item.page_id}/{item.flow_id} "
            f"({item.name or item.flow_id})",
            flush=True,
        )
        outcome = run_flow_strict(
            deps,
            session_id=session_id,
            page_id=item.page_id,
            flow_id=item.flow_id,
            strict=strict,
        )
        all_entries.extend(outcome.entries)
        all_failures.extend(outcome.failures)
        if outcome.paused or outcome.hard_fail:
            paused = True
            break
        if not auto_play:
            break

    return {
        "entries": all_entries,
        "failures": all_failures,
        "paused": paused,
        "turns": 1,
    }


def run_playlist_timeline(
    deps: CallDeps,
    *,
    session_id: UUID,
    auto_play: bool = True,
    strict: bool = True,
) -> dict:
    """Chain demo_playlist flows that have recorded playback metadata."""
    graph = deps.graph
    playlist = sorted(graph.demo_playlist, key=lambda x: x.order) if graph.demo_playlist else []
    if not playlist:
        return {"entries": [], "failures": [], "paused": False}

    all_entries: list[ActionLogEntry] = []
    all_failures: list[ActionLogEntry] = []
    paused = False

    for item in playlist:
        if _stopped(deps):
            break
        try:
            calls = list(graph.flow(item.page_id, item.flow_id))
        except SiteGraphError:
            calls = []
        if _flow_is_stub(calls):
            print(
                f"[timeline] skip empty/stub flow {item.page_id}/{item.flow_id} "
                f"({item.name or item.flow_id}) — re-record with real clicks",
                flush=True,
            )
            continue
        if graph.has_recorded_playback(item.flow_id):
            print(
                f"[timeline] flow {item.page_id}/{item.flow_id} "
                f"({item.name or item.flow_id})",
                flush=True,
            )
            outcome = run_flow_timeline(
                deps,
                session_id=session_id,
                page_id=item.page_id,
                flow_id=item.flow_id,
                strict=strict,
            )
        else:
            print(
                f"[timeline] flow {item.flow_id}: no narration — strict YAML",
                flush=True,
            )
            outcome = run_flow_strict(
                deps,
                session_id=session_id,
                page_id=item.page_id,
                flow_id=item.flow_id,
                strict=strict,
            )
        all_entries.extend(outcome.entries)
        all_failures.extend(outcome.failures)
        if outcome.paused or outcome.hard_fail:
            paused = True
            break
        if not auto_play:
            break

    return {
        "entries": all_entries,
        "failures": all_failures,
        "paused": paused,
        "turns": 1,
    }


def playlist_timeline_ready(graph) -> bool:
    """True when at least one playlist row can run on the timeline engine."""
    if not graph.demo_playlist:
        return False
    return any(graph.has_recorded_playback(item.flow_id) for item in graph.demo_playlist)
