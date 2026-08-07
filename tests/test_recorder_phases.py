"""Recorder setup/capturing phase gate + password value drop."""

from __future__ import annotations

from navigator.automation.login_match import (
    LoginConfig,
    VAULT_PASSWORD_SENTINEL,
    is_password_field,
)
from navigator.automation.record import CaptureGate, NarrationCapture, _step_from_payload
from navigator.client.content import RecorderJob, begin_capture, recorder_status
import navigator.client.content as content


def test_password_fill_becomes_sentinel():
    step = _step_from_payload(
        {
            "tool": "fill_field",
            "tag": "input",
            "id": "password",
            "type": "password",
            "value": "typed-secret",
        }
    )
    assert step.value == VAULT_PASSWORD_SENTINEL
    assert "typed-secret" not in (step.value or "")


def test_setup_phase_discards_then_capture_keeps():
    steps = []
    gate = CaptureGate(phase="setup", login_config_fn=lambda: LoginConfig())
    # Simulate _on_payload gate logic without Playwright.
    from navigator.automation.record import RecordedStep

    def ingest(payload):
        step = _step_from_payload(payload)
        if gate.phase != "capturing":
            gate.setup_discarded += 1
            return
        steps.append(step)

    ingest({"tool": "click_element", "id": "email", "tag": "input"})
    ingest({"tool": "fill_field", "id": "password", "type": "password", "tag": "input", "value": "x"})
    assert gate.setup_discarded == 2
    assert steps == []

    gate.phase = "capturing"
    ingest({"tool": "click_element", "id": "compose", "tag": "button"})
    assert len(steps) == 1
    assert steps[0].alias == "compose"


def test_capturing_flags_login_url_steps():
    steps = []
    gate = CaptureGate(
        phase="capturing",
        login_config_fn=lambda: LoginConfig(login_url="https://acme.example/login"),
    )
    from navigator.automation.login_match import looks_like_login

    payload = {
        "tool": "click_element",
        "id": "submit",
        "tag": "button",
        "url": "https://acme.example/login",
    }
    step = _step_from_payload(payload)
    reason = looks_like_login(
        config=gate.login_config_fn(),
        element={"type": "", "autocomplete": ""},
        url=payload["url"],
        selector=step.selector,
    )
    assert reason
    gate.flagged.append({"tool": step.tool, "reason": reason})
    assert steps == []
    assert len(gate.flagged) == 1


def test_recorder_status_exposes_narrate_flag():
    job = RecorderJob(job_id="t1", flow_name="demo", flow_id="demo", narration=NarrationCapture())
    content._active = job
    try:
        st = recorder_status()
        assert st["narrate"] is True
        assert st["narration_chunks"] == 0
        assert st["save_mode"] == "new"
    finally:
        content._active = None


def test_start_recorder_update_requires_flow_id():
    import pytest

    with pytest.raises(RuntimeError, match="flow_id required"):
        from navigator.client.content import start_recorder

        start_recorder(
            start_url="https://acme.example/",
            flow_name="Tour",
            save_mode="update",
        )
