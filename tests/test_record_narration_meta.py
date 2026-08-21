"""Recorded narration metadata always saved even when STT fails."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from navigator.app.main import _attach_recorded_narration
from navigator.automation.record import RecordedStep
from navigator.knowledge.site_graph import parse_site_graph


def _minimal_yaml() -> str:
    return (
        "version: 1\n"
        "site: acme\n"
        "base_url: https://app.acme.test/\n"
        "pages:\n"
        "  home:\n"
        "    name: Home\n"
        "    url: /\n"
        "    selectors:\n"
        "      signup: text=Sign up\n"
        "    flows:\n"
        "      demo: []\n"
    )


class _Narration:
    def audio(self) -> bytes:
        return b"fake-audio"

    language = "auto"
    translate_to = "same"


class _Job:
    flow_id = "demo"
    flow_name = "Demo"
    steps = [
        RecordedStep(
            tool="click_element",
            alias="signup",
            selector="signup",
            at_ms=1200,
            mouse_path=[{"x": 10, "y": 20, "at_ms": 1100}],
        ),
    ]
    narration = _Narration()


def test_attach_recorded_narration_saves_clicks_when_stt_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(**_kwargs):
        raise RuntimeError("429 rate limit")

    monkeypatch.setattr(
        "navigator.automation.narration.narrate_recording",
        _boom,
    )
    monkeypatch.setattr(
        "navigator.core.groq_keys.groq_key_candidates",
        lambda **_: ["test-key"],
    )

    yaml_out, narrated = _attach_recorded_narration(_minimal_yaml(), _Job())
    graph = parse_site_graph(yaml_out)
    # STT fail → silent lines (no invented "Here is signup.")
    assert graph.flow_narration_lines("demo") == [""]
    assert 0 in graph.flow_step_clicks("demo")
    assert narrated == 0
    paths = graph.flow_step_mouse_paths("demo")
    assert paths[0][0]["x"] == 10
    # No speech → not a narrated playback timeline.
    assert graph.has_recorded_playback("demo") is False
    speech = graph.flow_step_speech("demo")
    assert 0 not in speech


def test_attach_recorded_narration_saves_speech_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "navigator.automation.narration.narrate_recording",
        lambda **_kwargs: (["Click sign up."], [{"idx": 0, "speak_ms": 900}], [(300, 1100)]),
    )
    monkeypatch.setattr(
        "navigator.core.groq_keys.groq_key_candidates",
        lambda **_: ["test-key"],
    )

    yaml_out, narrated = _attach_recorded_narration(_minimal_yaml(), _Job())
    graph = parse_site_graph(yaml_out)
    assert narrated == 1
    # Compact demo clock: click lands during the spoken line.
    speech = graph.flow_step_speech("demo")
    clicks = graph.flow_step_clicks("demo")
    assert 0 in speech and 0 in clicks
    assert speech[0][0] <= clicks[0] <= speech[0][1]


def test_rebuild_yaml_narration_splits_monologue_and_fills_silent_clicks():
    from navigator.client.content import rebuild_yaml_narration

    mono = (
        "Welcome to the dashboard where you can see every conversation at a glance. "
        "From here the team tracks replies, tags, and assignments in one place. "
        "Click campaigns to open the list of running outreach. "
        "Then create a new one and pick the audience you want to reach. "
        "Fill in the name and the message template before you continue. "
        "Save when you are done and watch it go live for the team."
    )
    yaml_in = (
        "version: 1\n"
        "site: acme\n"
        "base_url: https://app.acme.test/\n"
        "pages:\n"
        "  home:\n"
        "    name: Home\n"
        "    url: /\n"
        "    selectors:\n"
        "      body: body\n"
        "      dashboard: text=Dashboard\n"
        "      campaigns: text=Campaigns\n"
        "      create: text=Create\n"
        "    flows:\n"
        "      demo:\n"
        "        - tool: click_element\n"
        "          selector: dashboard\n"
        "          expects: {check: visible, selector: body}\n"
        "        - tool: click_element\n"
        "          selector: campaigns\n"
        "          expects: {check: visible, selector: body}\n"
        "        - tool: click_element\n"
        "          selector: create\n"
        "          expects: {check: visible, selector: body}\n"
        "_meta:\n"
        "  narration_suggestions:\n"
        "    demo:\n"
        "      - |\n"
        f"        {mono}\n"
        "      - ''\n"
        "      - ''\n"
        "  step_clicks:\n"
        "    demo:\n"
        "      - {idx: 0, at_ms: 500}\n"
        "      - {idx: 1, at_ms: 4000}\n"
        "      - {idx: 2, at_ms: 8000}\n"
        "  step_speech:\n"
        "    demo:\n"
        "      - {idx: 0, start_ms: 0, end_ms: 45000}\n"
    )
    yaml_out = rebuild_yaml_narration(yaml_in, flow_id="demo")
    graph = parse_site_graph(yaml_out)
    lines = graph.flow_narration_lines("demo")
    assert len(lines) == 3
    assert all(l.strip() for l in lines)
    assert all(len(l.split()) <= 50 for l in lines)
    speech = graph.flow_step_speech("demo")
    assert 0 in speech and 1 in speech and 2 in speech
    assert speech[0][1] - speech[0][0] < 30_000
    clicks = graph.flow_step_clicks("demo")
    assert clicks[2] < 60_000

