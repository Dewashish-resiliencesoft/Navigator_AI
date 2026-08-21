"""Tests for prompt command markers + AgentTask parse."""

from __future__ import annotations

from navigator.automation.prompt_command import (
    AgentTask,
    AgentTaskStep,
    detect_marker_in_text,
    heuristic_parse_instruction,
    is_marker_start,
    is_marker_stop,
    merge_agent_tasks_into_meta,
    parse_agent_task_instruction,
    strip_prompt_markers,
)


def test_exact_markers():
    assert is_marker_start("prompt start")
    assert is_marker_start("  Prompt Start  ")
    assert is_marker_stop("prompt stop")
    assert not is_marker_start("please prompt start now")  # whole-utterance preferred
    assert detect_marker_in_text("ok prompt start please") == "start"
    assert detect_marker_in_text("and prompt stop") == "stop"


def test_strip_markers_from_narration():
    text = "Here we open contacts. prompt start Ask for phone prompt stop Then we continue."
    cleaned = strip_prompt_markers(text)
    assert "prompt start" not in cleaned.lower()
    assert "prompt stop" not in cleaned.lower()
    assert "open contacts" in cleaned.lower()
    assert "continue" in cleaned.lower()


def test_heuristic_ask_fill_phone():
    task = heuristic_parse_instruction(
        "Ask the visitor for their phone number, save it as phone_number, "
        "and fill this phone field with it.",
        current_field={"alias": "phone", "selector": "#phone", "step_index": 2},
    )
    ops = [s.op for s in task.steps]
    assert "ask_user" in ops
    assert "fill_field" in ops
    assert any(s.variable == "phone_number" for s in task.steps)
    assert task.step_index == 2


def test_parse_without_llm_uses_heuristic():
    task = parse_agent_task_instruction(
        "Use the saved contact phone_number later",
        use_llm=False,
    )
    assert task.steps
    assert task.status == "draft"


def test_merge_agent_tasks_meta():
    t = AgentTask(id="abc", raw_instruction="x", steps=[], status="confirmed")
    meta = merge_agent_tasks_into_meta({}, [t])
    assert meta["agent_tasks"][0]["id"] == "abc"
    meta2 = merge_agent_tasks_into_meta(meta, [t])
    assert len(meta2["agent_tasks"]) == 1


def test_apply_confirmed_marks_ask():
    from navigator.automation.record import RecordedStep
    from navigator.automation.record_studio import apply_confirmed_agent_task

    steps = [
        RecordedStep(
            tool="fill_field",
            alias="phone",
            selector="#phone",
            value="555",
            page_id="p",
            at_ms=0,
        )
    ]
    task = AgentTask(
        id="t1",
        raw_instruction="ask phone",
        steps=[
            AgentTaskStep(op="ask_user", variable="phone_number", question="Phone?"),
            AgentTaskStep(op="fill_field", variable="phone_number", step_index=0),
        ],
        step_index=0,
        status="confirmed",
    )
    apply_confirmed_agent_task(steps, task)
    assert steps[0].source == "user"
    assert steps[0].alias == "phone_number"


def test_run_agent_task_ask_stores_context():
    from navigator.agent.prompt_tasks import run_agent_task

    answers: dict[str, str] = {}
    notes = run_agent_task(
        AgentTask(
            id="x",
            raw_instruction="ask",
            steps=[AgentTaskStep(op="ask_user", variable="phone", question="Phone?")],
        ),
        live_answers=answers,
        listen_once=lambda _q: "555-0100",
    )
    assert answers["phone"] == "555-0100"
    assert notes
