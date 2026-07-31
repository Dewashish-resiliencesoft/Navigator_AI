"""VERIFYING: did the declared postcondition actually come true?

Pure DOM comparison, no LLM. Writes an ActionLogEntry either way -- the log is the
record of what the agent expected versus what it got, and that is only useful if
successes are in there too.
"""

from __future__ import annotations

from navigator.agent.state import CallDeps, CallState
from navigator.browser.verify import check
from navigator.logs.store import utcnow
from navigator.schemas import ActionLogEntry, FillField, VerifyResult


def verifying(state: CallState, deps: CallDeps) -> CallState:
    call, result = state.get("last_call"), state.get("last_result")
    if call is None or result is None:
        return CallState()

    # page_id after the call: a navigate's postcondition belongs to where it landed.
    verified_on = state["page_id"]

    if result.ok:
        verdict = check(deps.page, deps.graph, verified_on, call.expects)
    else:
        # The action itself failed, so the postcondition was never reachable.
        verdict = VerifyResult(passed=False, actual=f"action failed: {result.detail}")

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
    return CallState(
        entries=[entry],
        failures=[entry] if entry.failed else [],
        narration=[line],
        transcript=[f"agent: {line}"],
    )


def _narrate(entry: ActionLogEntry, verdict: VerifyResult) -> str:
    """What SPEAKING says about this step. Honest about failure -- a prospect can
    see the screen, so pretending it worked is worse than admitting it didn't."""
    call = entry.tool_call
    if verdict.passed:
        return _success_line(call)
    return (
        f"That didn't do what I expected. I was looking for "
        f"{call.expects.check.replace('_', ' ')} on {call.expects.selector}, "
        f"but I got: {verdict.actual}. Let me note that and keep going."
    )


def _success_line(call) -> str:
    match call.tool:
        case "navigate":
            return f"Okay, we're on the {call.page_id} page now."
        case "fill_field":
            whose = "the value you gave me" if call.source == "user" else "some text"
            return f"I've typed {whose} into the {_human(call.selector)}."
        case "click_element":
            return f"And clicking the {_human(call.selector)}."
        case "wait_for":
            return f"The {_human(call.selector)} has loaded."
        case _:
            return "Done."


def _human(alias: str) -> str:
    return alias.replace("_", " ")
