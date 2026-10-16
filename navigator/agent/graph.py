"""The call state machine.

An explicit graph, not an agent loop. The interesting property is the
EXECUTING -> VERIFYING -> SPEAKING cycle: it repeats once per tool call, so the
agent never builds on top of an action it hasn't verified.

    joining -> introducing ------+
                                 v
              listening -> planning -> speaking <-- verifying <-- executing
                  ^                       |                          ^
                  |          +------------+-------------+------------+
                  |          |            |             |     pending calls
                  |     no plan yet   failures      all done
                  |          |            |             |
                  +----------+            v             v
                                     reflecting --> turn_done
                                                        |
                                                   turns left?
                                                     /      \
                                                    v        v
                                              listening    ending

Everything that talks routes through SPEAKING, so one node owns all TTS and
narration can't be spoken twice or dropped. `after_speaking` decides where to go
based on why SPEAKING was reached.

Speak∥act: when ``pending_calls`` is set, SPEAKING skips TTS and EXECUTING
starts the narration line as the cursor moves, then waits for that line
before the next action. VERIFYING runs before the next line, so speech does
not get ahead of a page that has not settled. The previous line is still
waited out before ``run_tool``.

Every node is a plain function of (CallState, CallDeps) returning a partial state,
so each is testable on its own with a dict and a fake CallDeps -- no graph, no
browser, no LangGraph.
"""

from __future__ import annotations

from functools import partial
from typing import Literal
from uuid import UUID

from langgraph.graph import END, StateGraph

from navigator.agent.nodes.ending import ending
from navigator.agent.nodes.executing import executing
from navigator.agent.nodes.introducing import introducing
from navigator.agent.nodes.joining import joining
from navigator.agent.nodes.listening import listening
from navigator.agent.nodes.planning import planning
from navigator.agent.nodes.reflecting import reflecting
from navigator.agent.nodes.speaking import speaking
from navigator.agent.nodes.verifying import verifying
from navigator.agent.state import CLEAR, CallDeps, CallState, initial_state
from navigator.agent.utterance import stamp_narration

NODES = (
    ("joining", joining),
    ("introducing", introducing),
    ("listening", listening),
    ("planning", planning),
    ("executing", executing),
    ("verifying", verifying),
    ("speaking", speaking),
    ("reflecting", reflecting),
    ("ending", ending),
)


def after_speaking(
    state: CallState,
) -> Literal["listening", "executing", "reflecting", "turn_done", "ending"]:
    """SPEAKING is the single TTS owner, so several paths converge on it.

    Where it goes next depends on why it was reached: the intro (no plan yet),
    mid-plan (calls still pending), or end of turn.
    """
    if state.get("finished") or state.get("phase") == "ending":
        return "ending"
    if state.get("pending_calls"):
        return "executing"
    if state.get("plan") is None:
        return "listening"  # the intro was just spoken
    if state.get("failures"):
        # Only failures earn a reflection pass; a clean turn skips the LLM entirely.
        return "reflecting"
    return "turn_done"


def after_turn(state: CallState) -> Literal["listening", "ending"]:
    if state.get("phase") == "ending" or state.get("finished"):
        return "ending"
    return "listening" if _turns_left(state) else "ending"


def _turns_left(state: CallState) -> bool:
    return state.get("turns", 0) < state.get("max_turns", 1)


def turn_done(state: CallState, deps: CallDeps) -> CallState:
    """Bookkeeping: one completed listen -> speak cycle."""
    return CallState(turns=state.get("turns", 0) + 1)


def anything_else_entry_state(
    session_id: UUID,
    page_id: str,
    *,
    max_turns: int,
    walkthrough_flow_id: str = "",
) -> CallState:
    """State to enter SPEAKING with the post-demo Q&A prompt already queued."""
    from navigator.agent.end_policy import ANYTHING_ELSE
    from navigator.core.schemas import Plan

    state = initial_state(
        session_id,
        page_id,
        max_turns=max_turns,
        walkthrough_flow_id=walkthrough_flow_id,
        auto_play=False,
    )
    state["phase"] = "anything_else"
    state["plan"] = Plan(spoken_response=ANYTHING_ELSE, tool_calls=[])
    state["pending_calls"] = []
    state["narration"] = [ANYTHING_ELSE]
    return state


_STAMP_NODES = frozenset({"planning", "introducing", "verifying"})


SAFE_FALLBACK_LINE = "Good question. Let me make a note of that and come back to it, or I can show you anything else in the product meanwhile."
_FALLBACK_LINES = (
    SAFE_FALLBACK_LINE,
    "I want to give you an accurate answer on that, so I'll follow up after the call. What else would you like to see?",
    "Happy to dig into that with the team afterwards. Is there another part of the product you'd like me to show?",
)
_GUARDED_NODES = frozenset({"planning", "reflecting"})


def _last_user_text(state: CallState) -> str:
    for line in reversed(list(state.get("transcript") or [])):
        if isinstance(line, str) and line.startswith("user:"):
            return line[5:].strip()
    return ""


def _fallback_answer(state: CallState, deps: CallDeps) -> str:
    """Next provider (Gemini text) for a short spoken reply; then a safe canned line."""
    utter = _last_user_text(state)
    if utter:
        try:
            from navigator.knowledge.flow_authoring import default_llm

            llm = default_llm()
            if llm is not None:
                persona = getattr(getattr(deps, "graph", None), "persona", None)
                product = getattr(persona, "product_name", "") or "the product"
                text = llm(
                    f"You are presenting a live demo of {product} on a video call. "
                    "Reply to the attendee in 1-2 short spoken sentences. Be accurate: "
                    "if you are not sure, say you'll follow up, and offer to show something. "
                    "No markdown, no ids, no URLs.",
                    utter,
                )
                text = " ".join((text or "").split())
                from navigator.voice.live_agent import is_echo_of_heard

                if is_echo_of_heard(text, [utter]):
                    print("[graph] fallback reply just echoed the attendee — using a canned line", flush=True)
                    text = ""
                if text and len(text) < 400:
                    return text
        except Exception as exc:  # noqa: BLE001
            print(f"[graph] fallback provider failed: {type(exc).__name__}", flush=True)
    n = int(state.get("turns") or 0)
    return _FALLBACK_LINES[n % len(_FALLBACK_LINES)]


def _guard(name: str, fn, deps: CallDeps):
    """An LLM/provider error in planning or reflecting must never end the call.

    13:54 live demo: a retired Groq model raised NotFoundError inside planning,
    the graph died and the bot left the meeting. Now planning answers via the
    next provider (or a safe spoken line) and returns to listening; reflecting
    just becomes a no-op.
    """

    def _safe(state: CallState) -> CallState:
        try:
            return fn(state)
        except Exception as exc:  # noqa: BLE001
            print(f"[graph] {name} failed, degrading: {type(exc).__name__}: {exc}"[:400], flush=True)
            if name != "planning":
                return CallState()
            from navigator.core.schemas import Plan

            line = _fallback_answer(state, deps)
            return CallState(
                plan=Plan(spoken_response=line, tool_calls=[]),
                pending_calls=[],
                narration=[line],
                transcript=[f"agent: {line}"],
            )

    return _safe


def _bind_node(name: str, fn, deps: CallDeps):
    """Bind deps; stamp utterance ids onto narration at the graph edge."""
    bound = partial(fn, deps=deps)
    if name in _GUARDED_NODES:
        bound = _guard(name, bound, deps)
    if name not in _STAMP_NODES:
        return bound

    def _stamped(state: CallState) -> CallState:
        out = bound(state)
        raw = out.get("narration")
        if raw is CLEAR or not raw:
            return out
        out["narration"] = stamp_narration(state, raw, kind=name)
        return out

    return _stamped


def build_graph(deps: CallDeps, *, entry: str = "joining"):
    """Wire and compile the graph. `deps` is bound into every node."""
    builder = StateGraph(CallState)

    for name, fn in NODES:
        builder.add_node(name, _bind_node(name, fn, deps))
    builder.add_node("turn_done", partial(turn_done, deps=deps))

    allowed = {name for name, _ in NODES} | {"turn_done"}
    if entry not in allowed:
        raise ValueError(f"unknown graph entry {entry!r}")
    builder.set_entry_point(entry)
    builder.add_edge("joining", "introducing")
    builder.add_edge("introducing", "speaking")
    builder.add_edge("listening", "planning")
    builder.add_edge("planning", "speaking")
    builder.add_edge("executing", "verifying")
    builder.add_edge("verifying", "speaking")
    builder.add_edge("reflecting", "turn_done")

    builder.add_conditional_edges("speaking", after_speaking)
    builder.add_conditional_edges("turn_done", after_turn)
    builder.add_edge("ending", END)

    # A scripted turn is ~4 calls x 3 nodes; the cap only guards against a
    # routing bug looping forever.
    # 100 was hit by a real 12-step demo with Q&A turns (GraphRecursionError at 12:40).
    return builder.compile().with_config(recursion_limit=400)
