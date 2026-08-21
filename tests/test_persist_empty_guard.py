"""Empty recorder persist must not wipe an existing flow."""

from __future__ import annotations

from navigator.client.content import RecorderJob, persist_recorder_job


def test_persist_refuses_zero_steps(monkeypatch):
    job = RecorderJob(
        job_id="t",
        flow_name="adding leads",
        flow_id="adding_leads",
        product_id="resiliohub",
        steps=[],
        save_mode="update",
    )
    # Should not touch registry when empty.
    def boom(*_a, **_k):
        raise AssertionError("must not merge empty recording")

    monkeypatch.setattr(
        "navigator.client.content.merge_recorded_flow", boom
    )
    out = persist_recorder_job(job)
    assert out["ok"] is False
    assert "0 steps" in (out.get("error") or "")
    assert job.persist_result is out
