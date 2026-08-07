"""Timeline playback for recorded narrated flows.

Replays clicks at recorded inter-step pacing while TTS runs in parallel — the
same overlap the Client had during capture. LangGraph speak-then-click walkthrough
is the fallback when metadata is missing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from navigator.agent.live_input import needs_live_input, resolve_live_fill
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


LIVE_LEAD_IN_MS = 0
# Cap recorded idle gaps so speech/action stay in sync on Meet.
MAX_INTER_STEP_MS = 1200

PAUSE_LINE = "One moment — let me get that step right."
SKIP_LINE = "Moving on — we'll skip that step for now."

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


def _wait_ms(ms: float, deps: CallDeps) -> None:
    """Sleep up to ``ms``, pumping screenshare frames when a pusher is wired."""
    from navigator.voice.language import poll_barge_in_language_switch

    if ms <= 0:
        return
    ms = min(float(ms), float(MAX_INTER_STEP_MS))
    deadline = time.monotonic() + ms / 1000.0
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


def _click_schedule(graph, flow_id: str, n_steps: int) -> dict[int, int]:
    clicks = graph.flow_step_clicks(flow_id)
    if clicks:
        return clicks
    timing = graph.flow_step_timing(flow_id)
    if not timing:
        return {i: i * 3000 for i in range(n_steps)}
    cumulative = 0
    out: dict[int, int] = {}
    for i in range(n_steps):
        cumulative += int(timing.get(i, 0) or 0)
        out[i] = cumulative
    return out


def step_deltas(schedule: dict[int, int], n_steps: int) -> list[int]:
    """Inter-step wait ms derived from absolute recorded click times."""
    deltas: list[int] = []
    prev_at = 0
    for i in range(n_steps):
        at = int(schedule.get(i, i * 3000) or 0)
        if i == 0:
            deltas.append(0)
        else:
            deltas.append(min(MAX_INTER_STEP_MS, max(0, at - prev_at)))
        prev_at = at
    return deltas


def _start_speech(deps: CallDeps, line: str) -> PlaybackHandle | None:
    text = spoken_for_live_step(line)
    if not text.strip():
        return None
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
    schedule = _click_schedule(deps.graph, flow_id, len(calls))
    deltas = step_deltas(schedule, len(calls))

    current_page = page_id
    speech: PlaybackHandle | None = None

    for step, call in enumerate(calls):
        if _stopped(deps):
            break

        if step > 0:
            _wait_ms(float(deltas[step]), deps)
            if _stopped(deps):
                break

        junk = _junk_playback_call(deps, call, page_id=current_page)
        if junk:
            print(
                f"[timeline] skip junk step {step} on {flow_id!r}: {junk}",
                flush=True,
            )
            continue

        # Mid-demo "talk in Hindi" / settings already hi — apply before next line.
        from navigator.voice.language import poll_barge_in_language_switch

        poll_barge_in_language_switch(deps)

        # One narration line at a time — overlap speech with THIS click only.
        _wait_speech(speech)
        line = lines[step] if step < len(lines) else ""
        speech = _start_speech(deps, line)

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
                _wait_speech(speech)
                deps.speaker.say(PAUSE_LINE)
                outcome.paused = True
                outcome.hard_fail = True
                return outcome
            print(
                f"[timeline] click miss step {step} on {flow_id!r}, continuing: "
                f"{entry.actual_result.detail}",
                flush=True,
            )
            if _demo_origin(deps) == "dashboard_test":
                deps.speaker.say(SKIP_LINE)
            continue

        print(
            f"[timeline] verify miss step {step} on {flow_id!r}, continuing: "
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
        _start_speech(deps, line)
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


def _prefetch_playlist_narration(deps: CallDeps, graph) -> None:
    """Warm TTS cache for timeline lines before playback starts."""
    speaker = deps.speaker
    prefetch = getattr(speaker, "prefetch_lines", None)
    if prefetch is None:
        return
    lines: list[str] = []
    for item in graph.demo_playlist or []:
        if not graph.has_recorded_playback(item.flow_id):
            continue
        for line in graph.flow_narration_lines(item.flow_id):
            text = spoken_for_live_step(line)
            if text.strip():
                lines.append(text)
    if lines:
        prefetch(lines)


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

    _prefetch_playlist_narration(deps, graph)

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
