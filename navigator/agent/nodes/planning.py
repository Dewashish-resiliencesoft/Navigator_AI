"""PLANNING: decide what to do and what to say.

Scripted path (CallDeps.scripted_flow set): replay a named flow — deterministic,
used by demo/CI. LLM path: retrieve memory, pick a flow_id via Groq or an
injectable chooser, expand tool_calls from the site graph. The model never
invents selectors or postconditions. flow_id null → confidential handoff, no tools.

Live walkthrough path: advance one tool per turn from walkthrough_flow_id; real
user asks interrupt via choose_flow without clearing walkthrough_step. After the
walkthrough, anything_else handles goodbye and silence policy D.
"""

from __future__ import annotations

from navigator.agent.end_policy import ANYTHING_ELSE, WRAP_UP, is_goodbye, next_silence_action
from navigator.agent.planner import HANDOFF_SPOKEN, FlowChoice, choose_flow
from navigator.agent.state import CallDeps, CallState
from navigator.config.site_graph import SiteGraphError
from navigator.meeting.intake import format_with_intake
from navigator.memory.retrieval import retrieve_corrections, retrieve_product_knowledge
from navigator.schemas import Plan
from navigator.settings import settings

_CONTINUE = frozenset({"ok", "continue", "go on", "yes", "sure"})


def _guide_page_id(state: CallState) -> str:
    """Page that owns walkthrough + interrupt flows (not the browser's current page)."""
    return (
        state.get("walkthrough_page_id")
        or state.get("page_id")
        or ""
    )


def planning(state: CallState, deps: CallDeps) -> CallState:
    if deps.set_status is not None:
        deps.set_status("tailoring", "Tailoring…")
    if getattr(deps.speaker, "bot_ended", False):
        return CallState(
            plan=Plan(spoken_response="", tool_calls=[]),
            pending_calls=[],
            finished=True,
            phase="ending",
        )
    if deps.scripted_flow is not None:
        page_id, flow_id = deps.scripted_flow
        return _plan_from_flow(
            deps,
            page_id,
            flow_id,
            spoken=_describe(deps.graph.page(page_id).name, flow_id),
        )

    if state.get("user_correction"):
        return _plan_user_correction(state, deps)

    phase = state.get("phase") or "walkthrough"
    transcript = list(state.get("transcript") or [])
    utterance = _query_from_transcript(transcript)

    if utterance and is_goodbye(utterance):
        return CallState(
            phase="ending",
            plan=Plan(spoken_response=WRAP_UP, tool_calls=[]),
            pending_calls=[],
            narration=[WRAP_UP],
            transcript=[f"agent: {WRAP_UP}"],
        )

    if phase == "ending":
        return CallState(
            plan=Plan(spoken_response="", tool_calls=[]),
            pending_calls=[],
            finished=True,
        )

    if phase == "anything_else":
        return _plan_anything_else(state, deps, utterance=utterance, transcript=transcript)

    if utterance and not _is_continue(utterance):
        return _plan_interrupt(state, deps, utterance=utterance, transcript=transcript)

    return _plan_walkthrough_next(state, deps)


def _is_continue(utterance: str) -> bool:
    text = (utterance or "").strip().lower()
    return not text or text in _CONTINUE


def _plan_anything_else(
    state: CallState,
    deps: CallDeps,
    *,
    utterance: str,
    transcript: list[str],
) -> CallState:
    if is_goodbye(utterance):
        return CallState(
            phase="ending",
            plan=Plan(spoken_response=WRAP_UP, tool_calls=[]),
            pending_calls=[],
            narration=[WRAP_UP],
            transcript=[f"agent: {WRAP_UP}"],
        )

    if not utterance.strip():
        silence_rounds = int(state.get("silence_rounds") or 0)
        action = next_silence_action(silence_rounds=silence_rounds)
        if action == "reask":
            return CallState(
                phase="anything_else",
                silence_rounds=silence_rounds + 1,
                plan=Plan(spoken_response=ANYTHING_ELSE, tool_calls=[]),
                pending_calls=[],
                narration=[ANYTHING_ELSE],
                transcript=[f"agent: {ANYTHING_ELSE}"],
            )
        return CallState(
            phase="ending",
            plan=Plan(spoken_response=WRAP_UP, tool_calls=[]),
            pending_calls=[],
            narration=[WRAP_UP],
            transcript=[f"agent: {WRAP_UP}"],
        )

    choice = _resolve_flow_choice(state, deps, transcript=transcript)
    if choice.flow_id is None:
        return _plan_handoff(
            deps,
            query=utterance,
            spoken=HANDOFF_SPOKEN,
            phase="anything_else",
        )
    page_id = _guide_page_id(state)
    page = deps.graph.page(page_id)
    if choice.flow_id not in page.flows:
        flow_ids = sorted(page.flows)
        raise ValueError(f"flow_id {choice.flow_id!r} not in allowed {flow_ids}")
    return _plan_from_flow(
        deps,
        page_id,
        choice.flow_id,
        spoken=choice.spoken_response,
        phase="anything_else",
    )


def _plan_interrupt(
    state: CallState,
    deps: CallDeps,
    *,
    utterance: str,
    transcript: list[str],
) -> CallState:
    walkthrough_step = int(state.get("walkthrough_step") or 0)
    brain = _try_turn_brain(state, deps, utterance=utterance)
    if brain is not None:
        return brain

    choice = _resolve_flow_choice(state, deps, transcript=transcript)
    if choice.flow_id is None:
        return _plan_handoff(
            deps,
            query=utterance,
            spoken=HANDOFF_SPOKEN,
            phase="walkthrough",
            walkthrough_step=walkthrough_step,
        )
    page_id = _guide_page_id(state)
    page = deps.graph.page(page_id)
    if choice.flow_id not in page.flows:
        flow_ids = sorted(page.flows)
        raise ValueError(f"flow_id {choice.flow_id!r} not in allowed {flow_ids}")
    return _plan_from_flow(
        deps,
        page_id,
        choice.flow_id,
        spoken=choice.spoken_response,
        phase="walkthrough",
        walkthrough_step=walkthrough_step,
    )


def _use_turn_brain(deps: CallDeps) -> bool:
    if deps.decide_turn is not None:
        return True
    if deps.use_turn_brain is False:
        return False
    if deps.use_turn_brain is True:
        return bool(settings.gemini_api_key)
    return bool(settings.gemini_api_key)


def _try_turn_brain(
    state: CallState, deps: CallDeps, *, utterance: str
) -> CallState | None:
    if not _use_turn_brain(deps):
        return None
    from navigator.agent.turn_brain import TurnDecision, capture_screenshot_png, decide_turn
    from navigator.schemas import Navigate, Postcondition

    walkthrough_step = int(state.get("walkthrough_step") or 0)
    allowed = set(deps.graph.pages.keys())
    screen = ""
    if deps.screen_context is not None:
        try:
            screen = deps.screen_context() or ""
        except Exception as exc:  # noqa: BLE001
            print(f"[plan] screen_context skipped: {exc}", flush=True)
    try:
        png = capture_screenshot_png(deps.page)
    except Exception as exc:  # noqa: BLE001
        print(f"[plan] screenshot skipped: {exc}", flush=True)
        return None

    intake = deps.intake
    intake_summary = ""
    if intake is not None:
        intake_summary = (
            f"{intake.name} at {intake.company}, "
            f"{intake.business_type}, need={intake.looking_for}"
        )
    nav_labels = [p.name for p in deps.graph.pages.values()]
    try:
        if deps.decide_turn is not None:
            decision = deps.decide_turn(
                utterance=utterance,
                screenshot_png=png,
                screen_text=screen,
                allowed_pages=allowed,
                product_brief=deps.product_brief or "",
                intake_summary=intake_summary,
                nav_labels=nav_labels,
            )
            if not isinstance(decision, TurnDecision):
                decision = TurnDecision.model_validate(decision)
        else:
            decision = decide_turn(
                utterance=utterance,
                screenshot_png=png,
                screen_text=screen,
                allowed_pages=allowed,
                product_brief=deps.product_brief or "",
                intake_summary=intake_summary,
                nav_labels=nav_labels,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[plan] turn brain failed ({exc}); falling back", flush=True)
        return None

    spoken = decision.spoken_response.strip()
    base = dict(
        phase="walkthrough",
        walkthrough_step=walkthrough_step,
        narration=[spoken],
        transcript=[f"agent: {spoken}"],
    )

    if decision.intent == "end":
        return CallState(
            phase="ending",
            plan=Plan(spoken_response=spoken or WRAP_UP, tool_calls=[]),
            pending_calls=[],
            narration=[spoken or WRAP_UP],
            transcript=[f"agent: {spoken or WRAP_UP}"],
        )

    if decision.intent in {"speak", "clarify"}:
        return CallState(
            **base,
            plan=Plan(spoken_response=spoken, tool_calls=[]),
            pending_calls=[],
        )

    if decision.intent == "navigate_page" and decision.page_id:
        call = Navigate(
            page_id=decision.page_id,
            expects=Postcondition(
                check="url_matches",
                expected=decision.page_id,
                timeout_ms=15000,
            ),
        )
        return CallState(
            **base,
            plan=Plan(spoken_response=spoken, tool_calls=[call]),
            pending_calls=[call],
        )

    if decision.intent == "click_nav" and decision.nav_label:
        return CallState(
            **base,
            plan=Plan(spoken_response=spoken, tool_calls=[]),
            pending_calls=[],
            nav_click_label=decision.nav_label.strip(),
        )

    return CallState(
        **base,
        plan=Plan(spoken_response=spoken, tool_calls=[]),
        pending_calls=[],
    )


def _plan_walkthrough_next(state: CallState, deps: CallDeps) -> CallState:
    page_id = _guide_page_id(state)
    flow_id = state.get("walkthrough_flow_id") or ""
    if not flow_id:
        raise RuntimeError(
            "walkthrough phase requires walkthrough_flow_id on CallState "
            "(or CallDeps.scripted_flow for deterministic replay)"
        )
    step = int(state.get("walkthrough_step") or 0)
    try:
        calls = list(deps.graph.flow(page_id, flow_id))
    except SiteGraphError as exc:
        raise RuntimeError(
            f"walkthrough flow {flow_id!r} not found on page {page_id!r}"
        ) from exc
    if step >= len(calls):
        return CallState(
            phase="anything_else",
            plan=Plan(spoken_response=ANYTHING_ELSE, tool_calls=[]),
            pending_calls=[],
            narration=[ANYTHING_ELSE],
            transcript=[f"agent: {ANYTHING_ELSE}"],
            silence_rounds=0,
        )
    nxt = calls[step]
    spoken = (getattr(nxt, "spoken", None) or "").strip()
    if not spoken:
        spoken = (
            _describe(deps.graph.page(page_id).name, flow_id)
            if step == 0
            else "Next."
        )
    spoken = format_with_intake(spoken, deps.intake)
    # First walkthrough step: brief personal bridge if we know them.
    if step == 0 and deps.intake and deps.intake.name:
        need = deps.intake.looking_for or "what you asked about"
        spoken = (
            f"Alright {deps.intake.name}, focusing on {need}. {spoken}"
        )
    return CallState(
        phase="walkthrough",
        walkthrough_step=step + 1,
        plan=Plan(spoken_response=spoken, tool_calls=[nxt]),
        pending_calls=[nxt],
        narration=[spoken],
        transcript=[f"agent: {spoken}"],
    )


def _resolve_flow_choice(
    state: CallState, deps: CallDeps, *, transcript: list[str]
) -> FlowChoice:
    page_id = _guide_page_id(state)
    page = deps.graph.page(page_id)
    flow_ids = sorted(page.flows)
    if not flow_ids:
        raise RuntimeError(f"page {page_id!r} has no flows to choose from")

    chroma_path = (
        deps.chroma_path if deps.chroma_path is not None else settings.chroma_path
    )
    query = _query_from_transcript(transcript)
    corrections = retrieve_corrections(
        deps.product_id,
        query,
        page=page_id,
        tool_call_type=None,
        path=chroma_path,
    )
    knowledge = retrieve_product_knowledge(
        deps.product_id, query, path=chroma_path
    )
    persona = deps.graph.effective_persona()
    screen = ""
    if deps.screen_context is not None:
        try:
            screen = deps.screen_context() or ""
        except Exception as exc:  # noqa: BLE001
            print(f"[plan] screen_context skipped: {exc}", flush=True)
    chooser_kwargs = dict(
        page_id=page_id,
        flow_ids=flow_ids,
        transcript=transcript,
        corrections=corrections,
        knowledge=knowledge,
        persona=persona,
        intake=deps.intake,
        product_brief=deps.product_brief or "",
        screen_context=screen,
    )
    if deps.choose_flow is not None:
        choice = deps.choose_flow(**chooser_kwargs)
    else:
        api_key = (
            deps.groq_api_key
            if deps.groq_api_key is not None
            else settings.groq_api_key
        )
        if not api_key:
            raise RuntimeError(
                "PLANNING needs CallDeps.scripted_flow=(page_id, flow_id) "
                "or a Groq API key (CallDeps.groq_api_key / NAVIGATOR_GROQ_API_KEY)"
            )
        choice = choose_flow(api_key=api_key, **chooser_kwargs)
    if not isinstance(choice, FlowChoice):
        choice = FlowChoice.model_validate(choice)
    return choice


def _plan_user_correction(state: CallState, deps: CallDeps) -> CallState:
    """Log prospect correction as pending rule; no Playwright."""
    from navigator.memory.pending import PendingCorrectionStore

    query = _query_from_transcript(list(state.get("transcript") or []))
    spoken = (
        "Thanks — I've noted that correction. A human will review it before it "
        "changes how I demo."
    )
    entries = list(state.get("entries") or [])
    last = entries[-1] if entries else None
    store_path = deps.pending_db_path or settings.db_path
    store = PendingCorrectionStore(store_path)
    try:
        store.add(
            product_id=deps.product_id,
            session_id=state["session_id"],
            page=(last.page if last else state.get("page_id") or ""),
            tool_call_type=(last.tool_call.tool if last else "unknown"),
            rule=query or "user correction",
            source_call_id=(last.call_id if last else state["session_id"]),
        )
    finally:
        store.close()
    print(f"[correction] pending from user: {query!r}", flush=True)
    plan = Plan(spoken_response=spoken, tool_calls=[])
    return CallState(
        plan=plan,
        pending_calls=[],
        narration=[spoken],
        transcript=[f"agent: {spoken}"],
        user_correction=False,
    )


def _plan_handoff(deps: CallDeps, *, query: str, spoken: str, **extra) -> CallState:
    print(f"[handoff] out_of_scope: {query!r}", flush=True)
    plan = Plan(spoken_response=spoken, tool_calls=[])
    return CallState(
        plan=plan,
        pending_calls=[],
        narration=[plan.spoken_response],
        transcript=[f"agent: {plan.spoken_response}"],
        **extra,
    )


def _plan_from_flow(
    deps: CallDeps, page_id: str, flow_id: str, *, spoken: str, **extra
) -> CallState:
    calls = deps.graph.flow(page_id, flow_id)
    plan = Plan(spoken_response=spoken, tool_calls=list(calls))
    return CallState(
        plan=plan,
        pending_calls=list(plan.tool_calls),
        narration=[plan.spoken_response],
        transcript=[f"agent: {plan.spoken_response}"],
        **extra,
    )


def _query_from_transcript(transcript: list[str]) -> str:
    for line in reversed(transcript):
        if line.startswith("user:"):
            return line.removeprefix("user:").strip()
    return " ".join(transcript[-5:]) if transcript else ""


def _describe(page_name: str, flow_id: str) -> str:
    """Announce a flow using the customer's own naming.

    Reads flow ids as English the same way narration reads selector aliases, so a
    well-named site graph produces sensible speech on any product with no code
    change here.
    """
    flow_name = flow_id.replace("_", " ").replace("-", " ")
    return (
        f"Sure, let me show you. I'll walk through {flow_name} on the "
        f"{page_name} page, step by step."
    )
