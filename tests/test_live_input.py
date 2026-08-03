"""Phase 3: FillField source=user pauses for live prospect input."""

from __future__ import annotations

from uuid import uuid4

from navigator.agent.nodes.executing import executing
from navigator.agent.state import CallDeps, initial_state
from navigator.core.schemas import FillField, Postcondition
from navigator.logs.decisions import DecisionTraceStore
from navigator.voice.tts import PrintSpeaker


def _fill(*, source="agent", value="example-card", live_question=None):
    return FillField(
        selector="message_input",
        value=value,
        source=source,
        live_question=live_question,
        expects=Postcondition(
            check="value_equals",
            selector="message_input",
            expected=value,  # verifying may use resolved; tests focus on execute path
        ),
    )


def _deps(site_graph, page, log, tmp_path, **kw):
    base = dict(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        scripted_flow=None,
        product_id="acme",
        archive_dir=tmp_path / "archives",
        decision_db_path=tmp_path / "decisions.db",
    )
    base.update(kw)
    return CallDeps(**base)


def test_agent_source_never_pauses(site_graph, page, log, tmp_path):
    heard: list[str] = []

    def listen_once(prompt: str) -> str:
        heard.append(prompt)
        return "should-not-be-called"

    call = _fill(source="agent", value="agent-value")
    # expects must match what we fill
    call = call.model_copy(
        update={
            "expects": Postcondition(
                check="value_equals",
                selector="message_input",
                expected="agent-value",
            )
        }
    )
    state = initial_state(uuid4(), "inbox")
    state["pending_calls"] = [call]
    deps = _deps(site_graph, page, log, tmp_path, listen_once=listen_once)
    out = executing(state, deps)
    assert heard == []
    assert out["last_result"].ok
    assert page.input_value("#message-input") == "agent-value"


def test_user_source_pauses_and_uses_spoken_answer(site_graph, page, log, tmp_path):
    prompts: list[str] = []

    def listen_once(prompt: str) -> str:
        prompts.append(prompt)
        return "Launch checklist"

    call = _fill(
        source="user",
        value="fallback-example",
        live_question="What should this card be called?",
    )
    state = initial_state(uuid4(), "inbox")
    state["pending_calls"] = [call]
    deps = _deps(
        site_graph,
        page,
        log,
        tmp_path,
        listen_once=listen_once,
        extract_entity=lambda key, question, heard: heard.strip(),
    )
    out = executing(state, deps)
    assert prompts == ["What should this card be called?"]
    assert out["last_result"].ok
    assert page.input_value("#message-input") == "Launch checklist"
    assert "source=user" in out["last_result"].detail

    with DecisionTraceStore(tmp_path / "decisions.db") as store:
        rows = store.for_session(state["session_id"], "acme")
    assert len(rows) == 1
    assert rows[0].branch == "live_input"
    assert "Launch checklist" in rows[0].detail


def test_unclear_answer_reasks_once_then_example(site_graph, page, log, tmp_path):
    prompts: list[str] = []
    answers = ["um", "huh?"]  # both unclear

    def listen_once(prompt: str) -> str:
        prompts.append(prompt)
        return answers.pop(0) if answers else ""

    call = _fill(
        source="user",
        value="Stored Example",
        live_question="What should we name it?",
    )
    state = initial_state(uuid4(), "inbox")
    state["pending_calls"] = [call]
    deps = _deps(
        site_graph,
        page,
        log,
        tmp_path,
        listen_once=listen_once,
        extract_entity=lambda key, question, heard: heard.strip(),
    )
    out = executing(state, deps)
    assert len(prompts) == 2  # ask + one re-ask
    assert out["last_result"].ok
    assert page.input_value("#message-input") == "Stored Example"

    with DecisionTraceStore(tmp_path / "decisions.db") as store:
        rows = store.for_session(state["session_id"], "acme")
    assert rows[0].branch == "live_input"
    assert "fallback" in rows[0].detail.lower() or "example" in rows[0].detail.lower()
