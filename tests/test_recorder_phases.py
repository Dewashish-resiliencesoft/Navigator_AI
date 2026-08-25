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


def test_mp_gate_phase_roundtrip():
    from types import SimpleNamespace

    from navigator.client.content import _MpGate

    ns = SimpleNamespace(phase="setup")
    flagged: list = []
    gate = _MpGate(ns, flagged, [])
    assert gate.phase == "setup"
    gate.phase = "capturing"
    assert ns.phase == "capturing"
    assert gate.phase == "capturing"


def test_mp_gate_needs_merge_roundtrip():
    from types import SimpleNamespace

    from navigator.client.content import _MpGate

    ns = SimpleNamespace(phase="capturing", needs_merge=False)
    gate = _MpGate(ns, [], [])
    gate.needs_merge = True
    assert ns.needs_merge is True
    assert gate.needs_merge is True


def test_recorder_status_reads_mp_ns_phase():
    """Studio begin_capture flips worker ns — status must not stay on main gate setup."""
    from types import SimpleNamespace

    job = RecorderJob(job_id="t-mp", flow_name="demo", flow_id="demo")
    job.gate = CaptureGate(phase="setup")
    job.mp_ns = SimpleNamespace(phase="capturing", needs_merge=False, setup_discarded=0)
    content._active = job
    try:
        st = recorder_status()
        assert st["phase"] == "capturing"
        assert st["active"] is True
        assert st["needs_merge"] is False
    finally:
        content._active = None


def test_studio_stop_sets_needs_merge_keeps_active():
    from types import SimpleNamespace

    job = RecorderJob(job_id="t-nm", flow_name="demo", flow_id="demo")
    job.gate = CaptureGate(phase="capturing")
    job.steps = []
    job.mp_ns = SimpleNamespace(phase="stopping", needs_merge=True, setup_discarded=2)
    job.needs_merge = False
    job.done = False
    content._active = job
    try:
        st = recorder_status()
        assert st["needs_merge"] is True
        assert st["active"] is True
        assert st["phase"] == "stopping"
        assert st["done"] is False
    finally:
        content._active = None


def test_stop_recorder_clears_needs_merge():
    job = RecorderJob(job_id="t-clr", flow_name="demo", flow_id="demo")
    job.gate = CaptureGate(phase="stopping", needs_merge=True)
    job.needs_merge = True
    job.done = False
    content._active = job
    try:
        from navigator.client.content import stop_recorder

        out = stop_recorder()
        assert out.needs_merge is False
        assert out.done is True
        assert out.phase == "done"
        st = recorder_status()
        assert st["needs_merge"] is False
        assert st["active"] is False
    finally:
        content._active = None


def test_stop_recorder_returns_already_done_job():
    job = RecorderJob(job_id="t-done", flow_name="demo", flow_id="demo")
    job.done = True
    job.phase = "done"
    job.persist_result = {"ok": True, "steps": 3, "phase": "done"}
    content._active = job
    try:
        from navigator.client.content import stop_recorder

        out = stop_recorder()
        assert out is job
        assert out.persist_result["steps"] == 3
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
