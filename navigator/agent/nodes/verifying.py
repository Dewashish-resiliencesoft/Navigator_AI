"""VERIFYING: did the declared postcondition actually come true?

Pure DOM comparison, no LLM. Writes an ActionLogEntry either way -- the log is the
record of what the agent expected versus what it got, and that is only useful if
successes are in there too.

Session expiry is a narrow exception: if the postcondition fails *and* the page
looks like the product's login screen (not a permissions-denied page), re-run
the login gate silently, speak a short stall line, and retry the same step.
"""

from __future__ import annotations

from navigator.agent.state import CallDeps, CallState
from navigator.automation.browser.verify import check, check_with_vision
from navigator.automation.login_match import (
    LoginConfig,
    is_login_url,
    looks_like_permission_denied,
)
from navigator.logs.store import utcnow
from navigator.core.schemas import ActionLogEntry, FillField, Navigate, VerifyResult

SESSION_STALL_LINE = "One moment."
STRICT_STALL = "One moment — let me try that again."


def verifying(state: CallState, deps: CallDeps) -> CallState:
    call, result = state.get("last_call"), state.get("last_result")
    if call is None or result is None:
        return CallState()

    # page_id after the call: a navigate's postcondition belongs to where it landed.
    verified_on = state["page_id"]

    from navigator.agent.nodes.executing import _already_authenticated, _is_login_call

    if result.ok and (
        str(result.detail or "").startswith("skipped")
        or (_is_login_call(call) and _already_authenticated(deps.page))
    ):
        # Executing deliberately skipped this step (already signed in / not on
        # screen). Its postcondition (e.g. #email) can't hold — not a failure.
        verdict = VerifyResult(passed=True, actual=str(result.detail))
    elif (
        result.ok
        and getattr(call, "tool", "") == "click_element"
        and getattr(call.expects, "check", "") == "hidden"
        and getattr(call.expects, "selector", "") == getattr(call, "selector", "")
    ):
        # Recorder asserted the clicked control disappears; persistent nav
        # links (Phonebook, Inbox) never do. The click itself succeeded.
        verdict = VerifyResult(passed=True, actual="clicked persistent control")
    elif result.ok:
        verdict = check(deps.page, deps.graph, verified_on, call.expects)
        if verdict.ambiguous:
            try:
                verdict = check_with_vision(
                    deps.page, deps.graph, verified_on, call.expects
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[verify] vision fallback failed: {exc}", flush=True)
    else:
        # The action itself failed, so the postcondition was never reachable.
        verdict = VerifyResult(passed=False, actual=f"action failed: {result.detail}")

    if not verdict.passed and _try_session_recovery(state, deps, call):
        return CallState(
            pending_calls=[call, *list(state.get("pending_calls") or [])],
            last_call=None,
            last_result=None,
            narration=[SESSION_STALL_LINE],
            transcript=[f"agent: {SESSION_STALL_LINE}"],
            # No failures entry — this is not a flow-content bug.
        )

    entry = ActionLogEntry(
        session_id=state["session_id"],
        product_id=deps.product_id,
        page=state["last_page_id"],
        tool_call=call,
        expected_postcondition=call.expects,
        actual_result=result,
        verify=verdict,
        source=call.source if isinstance(call, FillField) else "agent",
        timestamp=utcnow(),
    )
    deps.log.append(entry)

    if not entry.failed and isinstance(call, Navigate):
        try:
            from navigator.logs.transcript_emit import emit_transcript

            url = ""
            try:
                url = deps.page.url or ""
            except Exception:  # noqa: BLE001
                url = ""
            emit_transcript(
                product_id=deps.product_id,
                session_id=state["session_id"],
                kind="screen",
                page_id=str(state.get("page_id") or ""),
                text=url or str(state.get("page_id") or "navigated"),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[transcript] screen emit failed: {exc}", flush=True)

    strict = getattr(deps, "strict_playlist", False)
    if entry.failed and strict:
        stall = STRICT_STALL if not result.ok else (
            "One moment — that didn't land on screen."
        )
        deps.speaker.say(stall)
        hold_step = int(state.get("executing_step") or state.get("walkthrough_step") or 0)
        return CallState(
            entries=[entry],
            failures=[entry],
            walkthrough_step=hold_step,
            phase="walkthrough",
            narration=[stall],
            transcript=[f"agent: {stall}"],
            pending_calls=[],
            last_call=None,
            last_result=None,
        )

    line = _narrate(entry, verdict, deps)  # wait_for fails stay silent — see _narrate
    narration = [line] if line.strip() else []
    transcript = [f"agent: {line}"] if line.strip() else []
    if (
        strict
        and verdict.passed
        and state.get("planned_next_step") is not None
    ):
        return CallState(
            entries=[entry],
            failures=[],
            narration=narration,
            transcript=transcript,
            walkthrough_step=int(state["planned_next_step"]),
        )
    out = CallState(
        entries=[entry],
        failures=[entry] if entry.failed else [],
        narration=narration,
        transcript=transcript,
    )
    if state.get("planned_next_step") is not None and not strict:
        # Always move on: holding the step on a soft-fail re-planned the same
        # step and re-spoke its line (narration looped 2x in the live log).
        out["walkthrough_step"] = int(state["planned_next_step"])
    return out


def _try_session_recovery(state: CallState, deps: CallDeps, call) -> bool:
    """Silent re-auth + retry when we landed on the login page mid-flow."""
    if deps.relogin is None or deps.page is None:
        return False
    config = deps.login_config
    if not isinstance(config, LoginConfig) or not config.login_url:
        return False
    try:
        page_url = deps.page.url or ""
        page_text = ""
        try:
            page_text = deps.page.inner_text("body", timeout=2000) or ""
        except Exception:  # noqa: BLE001
            page_text = ""
    except Exception:  # noqa: BLE001
        return False
    if looks_like_permission_denied(page_text=page_text, url=page_url):
        return False
    if not is_login_url(page_url, config):
        return False
    print(
        f"[verify] session expired (on {page_url}) — silent re-auth, "
        f"retry {getattr(call, 'tool', '?')}",
        flush=True,
    )
    try:
        return bool(deps.relogin())
    except Exception as exc:  # noqa: BLE001
        print(f"[verify] session re-auth failed: {exc}", flush=True)
        return False


def _narrate(entry: ActionLogEntry, verdict: VerifyResult, deps=None) -> str:
    """Prospect-facing line. Technical detail stays in the ActionLog only."""
    call = entry.tool_call
    if verdict.passed:
        return _success_line(call)
    # Never speak Playwright/CSS/timeout jargon on the call.
    print(
        f"[verify] soft-fail; selector={call.expects.selector!r} "
        f"detail={verdict.actual!r}",
        flush=True,
    )
    # Never apologise out loud for a soft-fail: log only, keep the demo moving.
    return ""


def _success_line(call) -> str:
    """Prospect-facing success line — or empty to stay silent.

    Mechanical clicks/fills already show on screen share. Saying
    "I've clicked that" after every step floods the call.
    """
    match call.tool:
        case "navigate":
            # Silent: every scripted step already speaks its own narration, and
            # the page_id slug ("page_automation_integrations") was being read
            # aloud as a duplicate filler line.
            return ""
        case "fill_field" | "click_element" | "wait_for":
            return ""
        case _:
            return ""
