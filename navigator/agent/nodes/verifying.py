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
from navigator.core.schemas import ActionLogEntry, FillField, VerifyResult

SESSION_STALL_LINE = "One moment."


def verifying(state: CallState, deps: CallDeps) -> CallState:
    call, result = state.get("last_call"), state.get("last_result")
    if call is None or result is None:
        return CallState()

    # page_id after the call: a navigate's postcondition belongs to where it landed.
    verified_on = state["page_id"]

    if result.ok:
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

    line = _narrate(entry, verdict)
    narration = [line] if line.strip() else []
    transcript = [f"agent: {line}"] if line.strip() else []
    return CallState(
        entries=[entry],
        failures=[entry] if entry.failed else [],
        narration=narration,
        transcript=transcript,
    )


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


def _narrate(entry: ActionLogEntry, verdict: VerifyResult) -> str:
    """Prospect-facing line. Technical detail stays in the ActionLog only."""
    call = entry.tool_call
    if verdict.passed:
        return _success_line(call)
    # Never speak Playwright/CSS/timeout jargon on the call.
    print(
        f"[verify] soft-fail spoken; selector={call.expects.selector!r} "
        f"detail={verdict.actual!r}",
        flush=True,
    )
    return (
        "Oh — something glitched on our side there, not yours. "
        "It's nothing you did. We're sorting it; I'll keep going."
    )


def _success_line(call) -> str:
    """Prospect-facing success line — or empty to stay silent.

    Mechanical clicks/fills already show on screen share. Saying
    "I've clicked that" after every step floods the call.
    """
    match call.tool:
        case "navigate":
            return f"Okay, we're on the {call.page_id} page now."
        case "fill_field" | "click_element" | "wait_for":
            return ""
        case _:
            return ""
