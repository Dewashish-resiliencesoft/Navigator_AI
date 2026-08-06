"""PLANNING: decide what to do and what to say.

Scripted path (CallDeps.scripted_flow set): replay a named flow — deterministic,
used by demo/CI. LLM path: retrieve memory, pick a flow_id via Groq or an
injectable chooser, expand tool_calls from the site graph. The model never
invents selectors or postconditions. flow_id null → confidential handoff, no tools.

Live walkthrough path: advance one tool per turn from walkthrough_flow_id; real
user speech routes through `_decide_live_turn`, which retrieves context and
branches on match confidence without clearing walkthrough_step. After the
walkthrough, anything_else handles goodbye and silence policy D.

Every live turn writes one DecisionTrace row -- including the boring ones.
"""

from __future__ import annotations

from navigator.agent.call_memory import CallMemory
from navigator.agent.end_policy import (
    ANYTHING_ELSE,
    QUESTION_ANSWERED,
    RESUME_AFTER_QUESTION,
    RESUME_AFTER_SILENCE,
    WRAP_UP,
    is_goodbye,
    next_silence_action,
)
from navigator.agent.planner import HANDOFF_SPOKEN, FlowChoice, choose_flow
from navigator.agent.phrasing import phrase_turn
from navigator.agent.speech_safety import REFUSE_SPOKEN, is_exfil_request
from navigator.agent.state import CallDeps, CallState
from navigator.knowledge.context import (
    HIGH_CONFIDENCE,
    MEDIUM_CONFIDENCE,
    flow_text,
    retrieve_context,
)
from navigator.knowledge.site_graph import SiteGraphError
from navigator.meeting.intake import format_with_intake
from navigator.knowledge.memory.retrieval import retrieve_corrections, retrieve_product_knowledge
from navigator.core.schemas import Plan
from navigator.core.settings import settings

_CONTINUE = frozenset({"ok", "continue", "go on", "yes", "sure"})
_AFFIRM = frozenset(
    {"yes", "yeah", "yep", "sure", "ok", "okay", "please", "go ahead", "do it",
     "that one", "correct", "right", "exactly", "the first", "the second"}
)
_NEGATE = frozenset(
    {"no", "nope", "nah", "not that", "never mind", "nevermind", "cancel",
     "skip", "don't", "do not"}
)


def _memory(deps: CallDeps) -> CallMemory:
    """Call-scoped memory, created on first use so old callers keep working."""
    mem = getattr(deps, "memory", None)
    if not isinstance(mem, CallMemory):
        mem = CallMemory()
        deps.memory = mem
    return mem


def _trace(deps: CallDeps, state: CallState, **kw) -> None:
    """Record one turn's decision. Never let logging break a live call."""
    from navigator.logs.decisions import DecisionTraceStore

    try:
        store = DecisionTraceStore(deps.decision_db_path or settings.db_path)
        try:
            store.record(
                product_id=deps.product_id, session_id=state.get("session_id", ""), **kw
            )
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[trace] skipped: {exc}", flush=True)


def _say(
    deps: CallDeps,
    *,
    intent: str,
    fallback: str,
    utterance: str = "",
    context: str = "",
    pacing: str = "neutral",
) -> str:
    """One spoken line via the phrasing layer, falling back to a template."""
    phrase = deps.phrase or phrase_turn
    api_key = deps.groq_api_key if deps.groq_api_key is not None else settings.groq_api_key
    try:
        line = phrase(
            intent=intent,
            utterance=utterance,
            context=context,
            memory=_memory(deps),
            pacing=pacing,
            persona_name=deps.graph.effective_persona().product_name,
            product_brief=deps.product_brief or "",
            fallback=fallback,
            api_key=api_key or None,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[plan] phrasing failed ({exc}); using template", flush=True)
        line = fallback
    line = (line or fallback).strip() or fallback
    _memory(deps).note_spoken(line)
    return line


def _guide_page_id(state: CallState) -> str:
    """Page that owns walkthrough + interrupt flows (not the browser's current page)."""
    return (
        state.get("walkthrough_page_id")
        or state.get("page_id")
        or ""
    )


def _section_knowledge_for_step(
    deps: CallDeps,
    *,
    page_id: str,
    flow_id: str,
    step_action: str,
) -> str:
    """Pull knowledge that matches this page/flow so narration can explain it."""
    try:
        page_name = deps.graph.page(page_id).name
    except Exception:  # noqa: BLE001
        page_name = page_id
    query = " ".join(
        part for part in (page_name, flow_id.replace("_", " "), step_action) if part
    ).strip()
    if not query:
        return ""
    chroma_path = (
        deps.chroma_path if deps.chroma_path is not None else settings.chroma_path
    )
    try:
        chunks = retrieve_product_knowledge(
            deps.product_id, query, k=3, path=chroma_path
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[plan] section knowledge skipped: {exc}", flush=True)
        return ""
    # Soft prefer chunks that mention the page/section name.
    needle = page_name.lower()
    ranked = sorted(
        chunks,
        key=lambda c: (0 if needle and needle in c.lower() else 1, -len(c)),
    )
    return "\n".join(ranked[:3])


def _ensure_browser_on_page(deps: CallDeps, page_id: str) -> None:
    """After a Topic detour, put the browser back where the Default step expects.

    Only navigates when the path diverged — stay put when already correct.
    """
    if deps.page is None or not page_id:
        return
    try:
        expected = deps.graph.url_for(page_id)
    except SiteGraphError:
        return
    from navigator.automation.login_match import same_page_path

    try:
        current = deps.page.url or ""
    except Exception:  # noqa: BLE001
        return
    if same_page_path(current, expected):
        return
    print(
        f"[plan] resume re-nav {current!r} → {expected!r} (page {page_id})",
        flush=True,
    )
    try:
        deps.page.goto(expected, wait_until="domcontentloaded", timeout=30_000)
    except Exception as exc:  # noqa: BLE001
        print(f"[plan] resume re-nav failed: {exc}", flush=True)


def _step_narration_hint(
    deps: CallDeps,
    *,
    page_id: str,
    flow_id: str,
    step: int,
    call: object,
) -> str:
    """Spoken hint priority: demo_script manual → YAML → semantics → explore → action."""
    from navigator.core.schemas import ClickElement, FillField, Navigate, WaitFor
    from navigator.knowledge.demo_script import resolve_flow_step_spoken
    from navigator.knowledge.site_graph import SiteGraphError

    if not isinstance(call, (ClickElement, FillField, Navigate, WaitFor)):
        return "Continuing."

    try:
        calls = deps.graph.flow(page_id, flow_id)
        step_count = len(calls)
    except SiteGraphError:
        step_count = step + 1

    try:
        page_name = deps.graph.page(page_id).name
    except SiteGraphError:
        page_name = page_id

    beat_id = f"flow_{flow_id}_{step}"
    spoken, _source = resolve_flow_step_spoken(
        graph=deps.graph,
        flow_id=flow_id,
        step_index=step,
        step_count=step_count,
        page_id=page_id,
        page_name=page_name,
        call=call,
        beat_id=beat_id,
    )
    return spoken or "Continuing."


def planning(state: CallState, deps: CallDeps) -> CallState:
    if deps.set_status is not None:
        deps.set_status("tailoring", "Tailoring…")
    if deps.set_avatar_state is not None:
        deps.set_avatar_state("thinking")
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

    if utterance and is_exfil_request(utterance):
        return CallState(
            plan=Plan(spoken_response=REFUSE_SPOKEN, tool_calls=[]),
            pending_calls=[],
            narration=[REFUSE_SPOKEN],
            transcript=[f"agent: {REFUSE_SPOKEN}"],
            phase=phase,
            walkthrough_step=state.get("walkthrough_step"),
        )

    if phase == "ending":
        return CallState(
            plan=Plan(spoken_response="", tool_calls=[]),
            pending_calls=[],
            finished=True,
        )

    if phase == "anything_else":
        return _plan_anything_else(state, deps, utterance=utterance, transcript=transcript)

    if phase == "awaiting_resume":
        return _plan_awaiting_resume(
            state, deps, utterance=utterance, transcript=transcript
        )

    if phase == "detour":
        if utterance and not _is_continue(utterance):
            return _plan_interrupt(state, deps, utterance=utterance, transcript=transcript)
        return _plan_detour_next(state, deps)

    # Clarifying-question follow-up must see "yes"/"ok" — those are also continue tokens.
    if (state.get("awaiting_confirm_flow_id") or "").strip() and utterance:
        return _plan_interrupt(state, deps, utterance=utterance, transcript=transcript)

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
            session_id=str(state["session_id"]),
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
    pending = (state.get("awaiting_confirm_flow_id") or "").strip()
    if pending:
        return _resolve_awaiting_confirm(
            state, deps, utterance=utterance, pending_flow_id=pending
        )

    decided = _decide_live_turn(state, deps, utterance=utterance)
    if decided is not None:
        return decided

    # Retrieval found nothing — optional Tier 2 (default OFF per product).
    tier2 = _try_tier2(state, deps, utterance=utterance)
    if tier2 is not None:
        return tier2

    # Retrieve found nothing actionable — vision turn-brain may still navigate.
    walkthrough_step = int(state.get("walkthrough_step") or 0)
    brain = _try_turn_brain(state, deps, utterance=utterance)
    if brain is not None:
        return brain

    spoken = _say(
        deps,
        intent="handoff",
        fallback=HANDOFF_SPOKEN,
        utterance=utterance,
        pacing=_memory(deps).classify_pacing(utterance),
    )
    _trace(
        deps,
        state,
        utterance=utterance,
        branch="handoff",
        spoken=spoken,
        detail="no flow match, no knowledge, tier2 skipped/off, turn-brain skipped",
    )
    return _plan_handoff(
        deps,
        query=utterance,
        spoken=spoken,
        phase="walkthrough",
        walkthrough_step=walkthrough_step,
        awaiting_confirm_flow_id=None,
        session_id=str(state["session_id"]),
    )


def _try_tier2(
    state: CallState, deps: CallDeps, *, utterance: str
) -> CallState | None:
    """Constrained live fallback. None when toggle off or proposer declines."""
    if not getattr(deps, "tier2_enabled", False):
        return None

    from navigator.agent.tier2 import pending_rule_for, run_tier2
    from navigator.agent.tier2_propose import bind_ephemeral_selector, propose_from_page
    from navigator.knowledge.memory.pending import PendingCorrectionStore

    walkthrough_step = int(state.get("walkthrough_step") or 0)
    page_id = _guide_page_id(state)

    propose = deps.tier2_propose
    if propose is None:
        if deps.page is None:
            return None

        def propose(**_kw):
            return propose_from_page(utterance=utterance, page=deps.page)

    outcome = run_tier2(
        utterance=utterance,
        propose=propose,
        classify=deps.tier2_classify,
    )
    if outcome is None:
        return None

    spoken = _say(
        deps,
        intent="handoff" if outcome.branch == "tier2_refused" else "flow_intro",
        fallback=outcome.spoken,
        utterance=utterance,
        context=outcome.detail,
        pacing=_memory(deps).classify_pacing(utterance),
    )
    _trace(
        deps,
        state,
        utterance=utterance,
        branch=outcome.branch,
        spoken=spoken,
        detail=outcome.detail,
    )

    if outcome.branch == "tier2_refused" or outcome.call is None:
        return CallState(
            phase="walkthrough",
            walkthrough_step=walkthrough_step,
            plan=Plan(spoken_response=spoken, tool_calls=[]),
            pending_calls=[],
            narration=[spoken],
            transcript=[f"agent: {spoken}"],
            awaiting_confirm_flow_id=None,
        )

    # Bind ephemeral alias so tools.execute can resolve the click.
    el = outcome.element or {}
    alias = str(el.get("_tier2_alias") or getattr(outcome.call, "selector", "") or "")
    css = str(el.get("_tier2_css") or "")
    if alias and css:
        deps.graph = bind_ephemeral_selector(deps.graph, page_id, alias, css)

    store_path = deps.pending_db_path or settings.db_path
    store = PendingCorrectionStore(store_path)
    try:
        store.add(
            product_id=deps.product_id,
            session_id=state.get("session_id", ""),
            page=page_id,
            tool_call_type=outcome.call.tool,
            rule=pending_rule_for(outcome, utterance),
            source_call_id=state.get("session_id", ""),
        )
    finally:
        store.close()

    return CallState(
        phase="detour",
        walkthrough_step=walkthrough_step,
        resume_step=walkthrough_step,
        resume_page_id=state.get("walkthrough_page_id") or page_id,
        detour_one_shot=True,
        detour_flow_id="",
        detour_page_id=page_id,
        detour_step=0,
        plan=Plan(spoken_response=spoken, tool_calls=[outcome.call]),
        pending_calls=[outcome.call],
        narration=[spoken],
        transcript=[f"agent: {spoken}"],
        awaiting_confirm_flow_id=None,
    )


def _is_affirm(utterance: str) -> bool:
    text = (utterance or "").strip().lower()
    if text in _AFFIRM:
        return True
    return any(text == w or text.startswith(w + " ") for w in _AFFIRM)


def _is_negate(utterance: str) -> bool:
    text = (utterance or "").strip().lower()
    if text in _NEGATE:
        return True
    return any(text.startswith(w) for w in _NEGATE)


def _flow_texts_for_page(deps: CallDeps, page_id: str) -> dict[str, str]:
    """Match text per flow: id, playlist name, and generated purpose + tags.

    The purpose is what makes retrieval work on a machine-explored product. A
    flow id like `explored_a1b2c3d4` carries no meaning, so ranking against the id
    alone can only ever match by luck; "Create and send an invoice" matches "how
    do I bill someone".

    Flows with an explicit `broken` / `needs_review` validation verdict are
    excluded so a rotten explored flow cannot reach a live End User. Flows with
    no validation entry (manually authored) stay offerable.
    """
    from navigator.automation.explore.validate import is_offerable

    page = deps.graph.page(page_id)
    names: dict[str, str] = {}
    for item in deps.graph.demo_playlist:
        if item.page_id == page_id and item.name.strip():
            names[item.flow_id] = item.name.strip()
    return {
        fid: flow_text(
            fid,
            name=names.get(fid, ""),
            trigger_intent=_flow_intent(deps, fid),
        )
        for fid in page.flows
        if is_offerable(deps.graph.flow_validation(fid))
    }


def _flow_intent(deps: CallDeps, flow_id: str) -> str:
    """Generated purpose + tags for a flow, as one string. Empty when absent."""
    sem = deps.graph.flow_semantics(flow_id)
    if not sem:
        return ""
    purpose = str(sem.get("purpose") or "").strip()
    tags = sem.get("tags")
    tag_text = " ".join(str(t).strip() for t in tags if str(t).strip()) if isinstance(tags, list) else ""
    triggers = sem.get("triggers")
    trig_text = " ".join(str(t).strip() for t in triggers if str(t).strip()) if isinstance(triggers, list) else ""
    return " — ".join(p for p in (purpose, tag_text, trig_text) if p)


def _knowledge_hits(
    result,
) -> list[tuple[str, float]]:
    return [(chunk.id or chunk.summary or "chunk", score) for chunk, score in result.knowledge_chunks]


def _resolve_awaiting_confirm(
    state: CallState,
    deps: CallDeps,
    *,
    utterance: str,
    pending_flow_id: str,
) -> CallState:
    """Medium-confidence follow-up: yes → run flow; no → clear and re-decide."""
    walkthrough_step = int(state.get("walkthrough_step") or 0)
    page_id = _guide_page_id(state)
    pacing = _memory(deps).note_turn(utterance)

    if _is_affirm(utterance):
        spoken = _seamless_detour_spoken(
            deps,
            page_id=page_id,
            flow_id=pending_flow_id,
            utterance=utterance,
            pacing=pacing,
            context=f"Confirmed flow: {pending_flow_id}",
        )
        _memory(deps).note_flow(pending_flow_id)
        _trace(
            deps,
            state,
            utterance=utterance,
            branch="flow_executed",
            spoken=spoken,
            chosen_flow_id=pending_flow_id,
            detail="prospect confirmed clarifying question",
        )
        return _start_detour(
            deps,
            state,
            page_id,
            pending_flow_id,
            spoken=spoken,
            walkthrough_step=walkthrough_step,
            walkthrough_page=state.get("walkthrough_page_id") or page_id,
        )

    if _is_negate(utterance):
        spoken = _say(
            deps,
            intent="handoff",
            fallback="No problem — what would you like to see instead?",
            utterance=utterance,
            pacing=pacing,
        )
        _trace(
            deps,
            state,
            utterance=utterance,
            branch="handoff",
            spoken=spoken,
            detail=f"declined confirm for {pending_flow_id}",
        )
        return CallState(
            phase="walkthrough",
            walkthrough_step=walkthrough_step,
            awaiting_confirm_flow_id=None,
            plan=Plan(spoken_response=spoken, tool_calls=[]),
            pending_calls=[],
            narration=[spoken],
            transcript=[f"agent: {spoken}"],
        )

    # Unclear answer to the clarify — re-run live decision on this utterance.
    cleared = {**state, "awaiting_confirm_flow_id": None}
    return _decide_live_turn(cleared, deps, utterance=utterance) or _plan_handoff(
        deps,
        query=utterance,
        spoken=HANDOFF_SPOKEN,
        phase="walkthrough",
        walkthrough_step=walkthrough_step,
        awaiting_confirm_flow_id=None,
    )


def _decide_live_turn(
    state: CallState,
    deps: CallDeps,
    *,
    utterance: str,
) -> CallState | None:
    """Retrieve + confidence bands. None → caller may try turn-brain / handoff."""
    from navigator.agent.brain_router import route_turn

    page_id = _guide_page_id(state)
    walkthrough_step = int(state.get("walkthrough_step") or 0)
    walkthrough_page = state.get("walkthrough_page_id") or page_id
    mem = _memory(deps)
    pacing = mem.note_turn(utterance)
    phase = state.get("phase") or "walkthrough"

    flow_texts = _flow_texts_for_page(deps, page_id)
    retrieve = deps.retrieve or retrieve_context
    chroma_path = (
        deps.chroma_path if deps.chroma_path is not None else settings.chroma_path
    )

    enriched = utterance
    if deps.screen_context is not None:
        try:
            screen = (deps.screen_context() or "")[:400]
            if screen.strip():
                enriched = f"{utterance} {screen}"
        except Exception:  # noqa: BLE001
            pass

    try:
        ruled = route_turn(
            utterance=utterance,
            phase=phase,
            graph=deps.graph,
            page_id=page_id,
            product_id=deps.product_id,
            flow_texts=flow_texts,
            chroma_path=chroma_path,
            retrieve=lambda q, *a, **kw: retrieve(
                enriched if q == utterance else q,
                deps.product_id,
                flow_texts=flow_texts,
                available_flow_ids=list(flow_texts),
                chroma_path=chroma_path,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[plan] route_turn failed ({exc}); falling back to retrieve", flush=True)
        ruled = None

    if ruled is not None and ruled.intent == "continue":
        return None
    if ruled is not None and ruled.intent in {"goodbye", "end"}:
        spoken = _say(deps, intent="handoff", fallback=WRAP_UP, utterance=utterance, pacing=pacing)
        _trace(deps, state, utterance=utterance, branch="ended", spoken=spoken, detail="goodbye")
        return _plan_handoff(deps, query=utterance, spoken=spoken, phase="ended", session_id=str(state["session_id"]))

    if ruled is not None and ruled.intent == "run_flow" and ruled.flow_id:
        flow_id = ruled.flow_id
        if _flow_offerable(deps, page_id, flow_id):
            spoken = _seamless_detour_spoken(
                deps,
                page_id=page_id,
                flow_id=flow_id,
                utterance=utterance,
                pacing=pacing,
                context=ruled.detail or f"trigger/intent match {flow_id}",
            )
            mem.note_flow(flow_id)
            _trace(
                deps,
                state,
                utterance=utterance,
                branch="flow_executed",
                spoken=spoken,
                chosen_flow_id=flow_id,
                flow_candidates=[(flow_id, ruled.confidence or 1.0)],
                knowledge_hits=[],
                detail=ruled.detail or "router run_flow",
            )
            return _start_detour(
                deps,
                state,
                page_id,
                flow_id,
                spoken=spoken,
                walkthrough_step=walkthrough_step,
                walkthrough_page=walkthrough_page,
            )

    try:
        result = retrieve(
            enriched,
            deps.product_id,
            flow_texts=flow_texts,
            available_flow_ids=list(flow_texts),
            chroma_path=chroma_path,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[plan] retrieve failed ({exc}); no live decision", flush=True)
        return None

    candidates = list(result.candidate_flows)
    # Prefer a flow the prospect has not already seen this call.
    actionable = [
        (fid, conf) for fid, conf in candidates if not mem.has_covered_flow(fid)
    ]
    ranked = actionable or candidates
    band_source = ranked[0] if ranked else None

    def _band_for(pair: tuple[str, float] | None) -> str:
        if pair is None or pair[1] < MEDIUM_CONFIDENCE:
            return "none"
        return "high" if pair[1] >= HIGH_CONFIDENCE else "medium"

    band = _band_for(band_source)
    k_hits = _knowledge_hits(result)
    flow_cands = [(fid, conf) for fid, conf in candidates]

    if band == "high" and band_source is not None:
        flow_id, conf = band_source
        if not _flow_offerable(deps, page_id, flow_id):
            band = "none"
        else:
            knowledge_bits = " ".join(
                (chunk.summary or chunk.text)[:240]
                for chunk, score in result.knowledge_chunks[:2]
                if score >= 0.25
            )
            spoken = _seamless_detour_spoken(
                deps,
                page_id=page_id,
                flow_id=flow_id,
                utterance=utterance,
                pacing=pacing,
                context=(
                    f"Matched flow {flow_id} at confidence {conf:.2f}. "
                    f"Product knowledge: {knowledge_bits or '(none)'}"
                ),
            )
            mem.note_flow(flow_id)
            _trace(
                deps,
                state,
                utterance=utterance,
                branch="flow_executed",
                spoken=spoken,
                chosen_flow_id=flow_id,
                flow_candidates=flow_cands,
                knowledge_hits=k_hits,
                detail=f"high confidence {conf:.2f}; seamless detour",
            )
            return _start_detour(
                deps,
                state,
                page_id,
                flow_id,
                spoken=spoken,
                walkthrough_step=walkthrough_step,
                walkthrough_page=walkthrough_page,
            )

    if band == "medium" and band_source is not None:
        flow_id, conf = band_source
        if _flow_offerable(deps, page_id, flow_id):
            spoken = _seamless_detour_spoken(
                deps,
                page_id=page_id,
                flow_id=flow_id,
                utterance=utterance,
                pacing=pacing,
                context=f"Matched flow {flow_id} at confidence {conf:.2f}",
            )
            mem.note_flow(flow_id)
            _trace(
                deps,
                state,
                utterance=utterance,
                branch="flow_executed",
                spoken=spoken,
                chosen_flow_id=flow_id,
                flow_candidates=flow_cands,
                knowledge_hits=k_hits,
                detail=f"medium confidence {conf:.2f}; offerable → direct detour",
            )
            return _start_detour(
                deps,
                state,
                page_id,
                flow_id,
                spoken=spoken,
                walkthrough_step=walkthrough_step,
                walkthrough_page=walkthrough_page,
            )
        label = flow_id.replace("_", " ").replace("-", " ")
        fallback = f"Want me to show you {label}?"
        spoken = _say(
            deps,
            intent="clarify",
            fallback=fallback,
            utterance=utterance,
            context=f"Candidate flow: {flow_id} (confidence {conf:.2f})",
            pacing=pacing,
        )
        _trace(
            deps,
            state,
            utterance=utterance,
            branch="clarifying_question",
            spoken=spoken,
            chosen_flow_id=flow_id,
            flow_candidates=flow_cands,
            knowledge_hits=k_hits,
            detail=f"medium confidence {conf:.2f}; not offerable → awaiting confirm",
        )
        return CallState(
            phase="walkthrough",
            walkthrough_step=walkthrough_step,
            awaiting_confirm_flow_id=flow_id,
            resume_step=walkthrough_step,
            resume_page_id=walkthrough_page,
            plan=Plan(spoken_response=spoken, tool_calls=[]),
            pending_calls=[],
            narration=[spoken],
            transcript=[f"agent: {spoken}"],
        )

    relevant = result.relevant_knowledge
    if relevant:
        chunk, score = relevant[0]
        context = chunk.text or chunk.summary
        spoken = _say(
            deps,
            intent="answer",
            fallback=(chunk.summary or chunk.text or "Here's what I know about that.")[
                :280
            ],
            utterance=utterance,
            context=context,
            pacing=pacing,
        )
        topic = chunk.summary or chunk.category or chunk.id or "topic"
        mem.note_topic(topic)
        mem.note_fact((chunk.summary or chunk.text)[:160])
        _trace(
            deps,
            state,
            utterance=utterance,
            branch="knowledge_only",
            spoken=spoken,
            flow_candidates=flow_cands,
            knowledge_hits=k_hits,
            detail=f"knowledge hit {score:.2f}; no flow run",
        )
        return CallState(
            phase="awaiting_resume",
            walkthrough_step=walkthrough_step,
            resume_step=walkthrough_step,
            resume_page_id=walkthrough_page,
            resume_checkin_pending=True,
            awaiting_confirm_flow_id=None,
            plan=Plan(spoken_response=spoken, tool_calls=[]),
            pending_calls=[],
            narration=[spoken],
            transcript=[f"agent: {spoken}"],
        )

    # Nothing relevant — Phase 4 Tier-2 hook attaches here later.
    return None


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
    from navigator.core.schemas import Navigate, Postcondition

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
    walkthrough_page = state.get("walkthrough_page_id") or _guide_page_id(state)
    resume = dict(
        walkthrough_step=walkthrough_step,
        resume_step=walkthrough_step,
        resume_page_id=walkthrough_page,
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
            phase="awaiting_resume",
            resume_checkin_pending=True,
            **resume,
            plan=Plan(spoken_response=spoken, tool_calls=[]),
            pending_calls=[],
            narration=[spoken],
            transcript=[f"agent: {spoken}"],
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
            phase="detour",
            detour_one_shot=True,
            detour_flow_id="",
            detour_page_id=decision.page_id,
            detour_step=0,
            **resume,
            plan=Plan(spoken_response=spoken, tool_calls=[call]),
            pending_calls=[call],
            narration=[spoken],
            transcript=[f"agent: {spoken}"],
        )

    if decision.intent == "click_nav" and decision.nav_label:
        return CallState(
            phase="awaiting_resume",
            **resume,
            plan=Plan(spoken_response=spoken, tool_calls=[]),
            pending_calls=[],
            nav_click_label=decision.nav_label.strip(),
            narration=[spoken],
            transcript=[f"agent: {spoken}"],
        )

    return CallState(
        phase="awaiting_resume",
        **resume,
        plan=Plan(spoken_response=spoken, tool_calls=[]),
        pending_calls=[],
        narration=[spoken],
        transcript=[f"agent: {spoken}"],
    )


def _flow_offerable(deps: CallDeps, page_id: str, flow_id: str) -> bool:
    """True when flow exists on the site graph and may be shown live."""
    from navigator.automation.explore.validate import is_offerable

    try:
        page = deps.graph.page(page_id)
    except SiteGraphError:
        return False
    if flow_id not in page.flows:
        return False
    try:
        list(deps.graph.flow(page_id, flow_id))
    except SiteGraphError:
        return False
    return is_offerable(deps.graph.flow_validation(flow_id))


def _seamless_detour_fallback(page_name: str, flow_id: str) -> str:
    label = flow_id.replace("_", " ").replace("-", " ")
    return f"Yes, we can show that too — here's {label} on {page_name}."


def _seamless_detour_spoken(
    deps: CallDeps,
    *,
    page_id: str,
    flow_id: str,
    utterance: str,
    pacing: str,
    context: str,
) -> str:
    page_name = deps.graph.page(page_id).name
    return _say(
        deps,
        intent="detour_intro",
        fallback=_seamless_detour_fallback(page_name, flow_id),
        utterance=utterance,
        context=context,
        pacing=pacing,
    )


def _start_detour(
    deps: CallDeps,
    state: CallState,
    page_id: str,
    flow_id: str,
    *,
    spoken: str,
    walkthrough_step: int,
    walkthrough_page: str,
    **extra,
) -> CallState:
    """Begin a step-by-step detour; main demo bookmark preserved in resume_*."""
    _ensure_browser_on_page(deps, page_id)
    try:
        calls = list(deps.graph.flow(page_id, flow_id))
    except SiteGraphError as exc:
        raise RuntimeError(
            f"detour flow {flow_id!r} not found on page {page_id!r}"
        ) from exc
    if not calls:
        return _enter_awaiting_resume(state, deps, preamble=spoken)

    nxt = calls[0]
    step_spoken = _spoken_for_flow_step(
        deps,
        page_id=page_id,
        flow_id=flow_id,
        step=0,
        call=nxt,
        intro=spoken,
    )
    _memory(deps).note_spoken(step_spoken)
    return CallState(
        phase="detour",
        detour_flow_id=flow_id,
        detour_page_id=page_id,
        detour_step=1,
        detour_one_shot=False,
        walkthrough_step=walkthrough_step,
        walkthrough_page_id=walkthrough_page,
        resume_step=walkthrough_step,
        resume_page_id=walkthrough_page,
        plan=Plan(spoken_response=step_spoken, tool_calls=[nxt]),
        pending_calls=[nxt],
        narration=[step_spoken],
        transcript=[f"agent: {step_spoken}"],
        awaiting_confirm_flow_id=None,
        **{k: v for k, v in extra.items() if k != "awaiting_confirm_flow_id"},
    )


def _spoken_for_flow_step(
    deps: CallDeps,
    *,
    page_id: str,
    flow_id: str,
    step: int,
    call: object,
    intro: str = "",
    resume_bridge: str = "",
) -> str:
    yaml_hint = _step_narration_hint(
        deps, page_id=page_id, flow_id=flow_id, step=step, call=call
    )
    yaml_hint = format_with_intake(yaml_hint, deps.intake)
    parts = [p for p in (intro, resume_bridge, yaml_hint) if p.strip()]
    spoken = " ".join(parts).strip()

    if _use_turn_brain(deps) and deps.page is not None:
        try:
            from navigator.agent.turn_brain import capture_screenshot_png
            from navigator.agent.vision_narrator import generate_narration

            png = capture_screenshot_png(deps.page)
            screen = ""
            if deps.screen_context is not None:
                screen = deps.screen_context() or ""
            intake_summary = ""
            if deps.intake:
                intake_summary = (
                    f"{deps.intake.name} at {deps.intake.company}, "
                    f"{deps.intake.business_type}, need={deps.intake.looking_for}"
                )
            tool = getattr(call, "tool", "") or type(call).__name__
            alias = getattr(call, "alias", "") or getattr(call, "page_id", "")
            step_action = f"{tool} {alias}".strip()
            section_knowledge = _section_knowledge_for_step(
                deps,
                page_id=page_id,
                flow_id=flow_id,
                step_action=step_action,
            )
            spoken = generate_narration(
                screenshot_png=png,
                screen_text=screen,
                narration_hint=spoken,
                intake_summary=intake_summary,
                product_brief=deps.product_brief or "",
                step_action=step_action,
                section_knowledge=section_knowledge,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[plan] vision narration skipped: {exc}", flush=True)
    return spoken


def _plan_detour_next(state: CallState, deps: CallDeps) -> CallState:
    if state.get("detour_one_shot"):
        return _enter_awaiting_resume(state, deps)

    page_id = state.get("detour_page_id") or _guide_page_id(state)
    flow_id = state.get("detour_flow_id") or ""
    step = int(state.get("detour_step") or 0)
    if not flow_id:
        return _enter_awaiting_resume(state, deps)

    _ensure_browser_on_page(deps, page_id)
    try:
        calls = list(deps.graph.flow(page_id, flow_id))
    except SiteGraphError as exc:
        raise RuntimeError(
            f"detour flow {flow_id!r} not found on page {page_id!r}"
        ) from exc

    if step >= len(calls):
        return _enter_awaiting_resume(state, deps)

    nxt = calls[step]
    spoken = _spoken_for_flow_step(
        deps, page_id=page_id, flow_id=flow_id, step=step, call=nxt
    )
    _memory(deps).note_spoken(spoken)
    _trace(
        deps,
        state,
        utterance="",
        branch="detour_step",
        spoken=spoken,
        chosen_flow_id=flow_id,
        detail=f"detour step {step} → {step + 1}",
    )
    return CallState(
        phase="detour",
        detour_flow_id=flow_id,
        detour_page_id=page_id,
        detour_step=step + 1,
        detour_one_shot=False,
        walkthrough_step=state.get("walkthrough_step"),
        walkthrough_page_id=state.get("walkthrough_page_id"),
        resume_step=state.get("resume_step"),
        resume_page_id=state.get("resume_page_id") or "",
        plan=Plan(spoken_response=spoken, tool_calls=[nxt]),
        pending_calls=[nxt],
        narration=[spoken],
        transcript=[f"agent: {spoken}"],
    )


def _enter_awaiting_resume(
    state: CallState, deps: CallDeps, *, preamble: str = ""
) -> CallState:
    spoken = _say(
        deps,
        intent="question_answered",
        fallback=QUESTION_ANSWERED,
        pacing=_memory(deps).pacing_history[-1]
        if _memory(deps).pacing_history
        else "neutral",
    )
    if preamble.strip():
        spoken = f"{preamble.strip()} {spoken}".strip()
    _trace(
        deps,
        state,
        utterance="",
        branch="awaiting_resume",
        spoken=spoken,
        detail="detour complete → check if question answered",
    )
    return CallState(
        phase="awaiting_resume",
        detour_flow_id="",
        detour_page_id="",
        detour_step=0,
        detour_one_shot=False,
        walkthrough_step=state.get("walkthrough_step"),
        walkthrough_page_id=state.get("walkthrough_page_id"),
        resume_step=state.get("resume_step"),
        resume_page_id=state.get("resume_page_id") or "",
        plan=Plan(spoken_response=spoken, tool_calls=[]),
        pending_calls=[],
        narration=[spoken],
        transcript=[f"agent: {spoken}"],
        awaiting_confirm_flow_id=None,
    )


def _plan_awaiting_resume(
    state: CallState,
    deps: CallDeps,
    *,
    utterance: str,
    transcript: list[str],
) -> CallState:
    if state.get("resume_checkin_pending"):
        if utterance and not _is_continue(utterance):
            if is_goodbye(utterance):
                return CallState(
                    phase="ending",
                    plan=Plan(spoken_response=WRAP_UP, tool_calls=[]),
                    pending_calls=[],
                    narration=[WRAP_UP],
                    transcript=[f"agent: {WRAP_UP}"],
                )
            if _is_affirm(utterance):
                return _plan_resume_main(
                    {**state, "resume_checkin_pending": False},
                    deps,
                    after_silence=False,
                )
            return _plan_interrupt(
                {**state, "resume_checkin_pending": False},
                deps,
                utterance=utterance,
                transcript=transcript,
            )
        spoken = _say(
            deps,
            intent="question_answered",
            fallback=QUESTION_ANSWERED,
            pacing=_memory(deps).pacing_history[-1]
            if _memory(deps).pacing_history
            else "neutral",
        )
        return CallState(
            phase="awaiting_resume",
            resume_checkin_pending=False,
            walkthrough_step=state.get("walkthrough_step"),
            walkthrough_page_id=state.get("walkthrough_page_id"),
            resume_step=state.get("resume_step"),
            resume_page_id=state.get("resume_page_id") or "",
            plan=Plan(spoken_response=spoken, tool_calls=[]),
            pending_calls=[],
            narration=[spoken],
            transcript=[f"agent: {spoken}"],
        )

    if utterance and is_goodbye(utterance):
        return CallState(
            phase="ending",
            plan=Plan(spoken_response=WRAP_UP, tool_calls=[]),
            pending_calls=[],
            narration=[WRAP_UP],
            transcript=[f"agent: {WRAP_UP}"],
        )

    if utterance and not _is_continue(utterance) and not _is_affirm(utterance):
        return _plan_interrupt(state, deps, utterance=utterance, transcript=transcript)

    if utterance.strip():
        return _plan_resume_main(state, deps, after_silence=False)

    return _plan_resume_main(state, deps, after_silence=True)


def _plan_resume_main(
    state: CallState, deps: CallDeps, *, after_silence: bool
) -> CallState:
    pacing = _memory(deps).pacing_history[-1] if _memory(deps).pacing_history else "neutral"
    bridge = _say(
        deps,
        intent="resume_silence" if after_silence else "resume_confirm",
        fallback=RESUME_AFTER_SILENCE if after_silence else RESUME_AFTER_QUESTION,
        pacing=pacing,
    )
    resume_state: CallState = {
        **state,
        "phase": "walkthrough",
    }
    cont = _plan_walkthrough_next(resume_state, deps)
    step_spoken = (cont.get("plan") or Plan(spoken_response="", tool_calls=[])).spoken_response
    combined = f"{bridge} {step_spoken}".strip() if step_spoken else bridge
    plan = cont.get("plan")
    if plan is not None:
        plan = Plan(spoken_response=combined, tool_calls=list(plan.tool_calls))
    return CallState(
        **{k: v for k, v in cont.items() if k not in ("plan", "narration", "transcript")},
        plan=plan,
        narration=[combined],
        transcript=[f"agent: {combined}"],
    )


def _plan_walkthrough_next(state: CallState, deps: CallDeps) -> CallState:
    page_id = _guide_page_id(state)
    flow_id = state.get("walkthrough_flow_id") or ""
    if not flow_id:
        raise RuntimeError(
            "walkthrough phase requires walkthrough_flow_id on CallState "
            "(or CallDeps.scripted_flow for deterministic replay)"
        )

    # After a Topic detour, resume at the remembered step/page.
    resume_step = state.get("resume_step")
    if resume_step is not None:
        step = int(resume_step)
        page_id = state.get("resume_page_id") or page_id
    else:
        step = int(state.get("walkthrough_step") or 0)

    _ensure_browser_on_page(deps, page_id)
    try:
        calls = list(deps.graph.flow(page_id, flow_id))
    except SiteGraphError as exc:
        raise RuntimeError(
            f"walkthrough flow {flow_id!r} not found on page {page_id!r}"
        ) from exc

    mem = _memory(deps)
    pacing = mem.pacing_history[-1] if mem.pacing_history else "neutral"

    # Coarse pacing nudge: several rushed signals → skip one step when safe.
    if pacing == "rushed" and step + 1 < len(calls) and resume_step is None:
        step = step + 1
        print(f"[plan] pacing=rushed; skip ahead to step {step}", flush=True)

    if step >= len(calls):
        auto_play = bool(state.get("auto_play", True))
        advanced = False
        if auto_play and deps.graph.demo_playlist:
            playlist = sorted(deps.graph.demo_playlist, key=lambda x: x.order)
            next_idx = -1
            for i, item in enumerate(playlist):
                if item.page_id == page_id and item.flow_id == flow_id:
                    next_idx = i + 1
                    break
            if next_idx >= 0 and next_idx < len(playlist):
                nxt_item = playlist[next_idx]
                page_id = nxt_item.page_id
                flow_id = nxt_item.flow_id
                step = 0
                try:
                    calls = list(deps.graph.flow(page_id, flow_id))
                except SiteGraphError as exc:
                    raise RuntimeError(
                        f"playlist next flow {flow_id!r} not found on page {page_id!r}"
                    ) from exc
                if calls:
                    advanced = True
                    _ensure_browser_on_page(deps, page_id)
                    print(
                        f"[plan] auto_play → next playlist flow "
                        f"{page_id}/{flow_id} ({nxt_item.name or flow_id})",
                        flush=True,
                    )
        if not advanced:
            spoken = ANYTHING_ELSE
            _trace(
                deps,
                state,
                utterance="",
                branch="continuation",
                spoken=spoken,
                detail="walkthrough exhausted → anything_else",
            )
            return CallState(
                phase="anything_else",
                plan=Plan(spoken_response=spoken, tool_calls=[]),
                pending_calls=[],
                narration=[spoken],
                transcript=[f"agent: {spoken}"],
                silence_rounds=0,
                resume_step=None,
                resume_page_id="",
            )
    nxt = calls[step]
    batch_calls, next_step = _batch_walkthrough_steps(
        calls, step, deps.graph, flow_id
    )
    if len(batch_calls) > 1:
        print(f"[plan] batch_safe: steps {step}..{next_step - 1}", flush=True)
    # YAML spoken as hint — vision generates the real narration.
    yaml_hint = _step_narration_hint(
        deps, page_id=page_id, flow_id=flow_id, step=step, call=nxt
    )
    yaml_hint = format_with_intake(yaml_hint, deps.intake)
    if step == 0 and deps.intake and deps.intake.name:
        need = deps.intake.looking_for or "what you asked about"
        yaml_hint = f"Alright {deps.intake.name}, focusing on {need}. {yaml_hint}"

    if resume_step is not None:
        yaml_hint = _say(
            deps,
            intent="resume",
            fallback=f"Back to where we were. {yaml_hint}",
            context=yaml_hint,
            pacing=pacing,
        )

    # Vision-first: agent looks at screen and generates narration.
    spoken = yaml_hint  # default if vision unavailable
    if _use_turn_brain(deps) and deps.page is not None:
        try:
            from navigator.agent.turn_brain import capture_screenshot_png
            from navigator.agent.vision_narrator import generate_narration

            png = capture_screenshot_png(deps.page)
            screen = ""
            if deps.screen_context is not None:
                screen = deps.screen_context() or ""
            intake_summary = ""
            if deps.intake:
                intake_summary = (
                    f"{deps.intake.name} at {deps.intake.company}, "
                    f"{deps.intake.business_type}, need={deps.intake.looking_for}"
                )
            # Describe what action is about to happen.
            step_action = ""
            tool = getattr(nxt, "tool", "") or type(nxt).__name__
            alias = getattr(nxt, "alias", "") or getattr(nxt, "page_id", "")
            step_action = f"{tool} {alias}".strip()

            section_knowledge = _section_knowledge_for_step(
                deps,
                page_id=page_id,
                flow_id=flow_id,
                step_action=step_action,
            )

            spoken = generate_narration(
                screenshot_png=png,
                screen_text=screen,
                narration_hint=yaml_hint,
                intake_summary=intake_summary,
                product_brief=deps.product_brief or "",
                step_action=step_action,
                section_knowledge=section_knowledge,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[plan] vision narration skipped: {exc}", flush=True)

    mem.note_spoken(spoken)
    _trace(
        deps,
        state,
        utterance="",
        branch="continuation",
        spoken=spoken,
        chosen_flow_id=flow_id,
        detail=f"walkthrough step {step} → {next_step}",
    )
    return CallState(
        phase="walkthrough",
        page_id=page_id,
        walkthrough_page_id=page_id,
        walkthrough_flow_id=flow_id,
        walkthrough_step=next_step,
        resume_step=None,
        resume_page_id="",
        plan=Plan(spoken_response=spoken, tool_calls=[batch_calls[0]]),
        pending_calls=list(batch_calls),
        narration=[spoken],
        transcript=[f"agent: {spoken}"],
    )


def _batch_walkthrough_steps(calls, step: int, graph, flow_id: str):
    """Emit consecutive safe Navigate steps when flow is batch_safe."""
    if step >= len(calls):
        return [calls[step]], step + 1
    validation = graph.flow_validation(flow_id)
    if not validation.get("batch_safe"):
        return [calls[step]], step + 1
    batch = [calls[step]]
    i = step + 1
    while i < len(calls):
        prev, curr = batch[-1], calls[i]
        prev_tool = getattr(prev, "tool", "") or type(prev).__name__
        curr_tool = getattr(curr, "tool", "") or type(curr).__name__
        if prev_tool == "navigate" and curr_tool == "navigate":
            batch.append(curr)
            i += 1
            continue
        break
    return batch, i


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
    """Log prospect correction; retry last step when possible."""
    from navigator.knowledge.memory.pending import PendingCorrectionStore

    query = _query_from_transcript(list(state.get("transcript") or []))
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
    if last is not None and last.tool_call is not None:
        spoken = "Got it — let me try that again with your correction in mind."
        plan = Plan(spoken_response=spoken, tool_calls=[last.tool_call])
        return CallState(
            plan=plan,
            pending_calls=[last.tool_call],
            narration=[spoken],
            transcript=[f"agent: {spoken}"],
            user_correction=False,
            phase=state.get("phase") or "walkthrough",
        )
    spoken = (
        "Thanks — I've noted that correction. A human will review it before it "
        "changes how I demo."
    )
    plan = Plan(spoken_response=spoken, tool_calls=[])
    return CallState(
        plan=plan,
        pending_calls=[],
        narration=[spoken],
        transcript=[f"agent: {spoken}"],
        user_correction=False,
    )


def _plan_handoff(
    deps: CallDeps,
    *,
    query: str,
    spoken: str,
    session_id: str | None = None,
    **extra,
) -> CallState:
    print(f"[handoff] out_of_scope: {query!r}", flush=True)
    url = (getattr(deps, "handoff_webhook_url", "") or "").strip()
    sid = session_id or extra.pop("session_id", None)
    if url and sid:
        try:
            import json
            import urllib.request

            payload = json.dumps(
                {"utterance": query, "session_id": str(sid), "spoken": spoken}
            ).encode()
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)  # noqa: S310
        except Exception as exc:  # noqa: BLE001
            print(f"[handoff] webhook failed: {exc}", flush=True)
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
