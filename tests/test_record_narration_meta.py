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
    assert graph.flow_step_clicks("demo") == {0: 1200}
    assert graph.flow_narration_lines("demo") == ["Here is signup."]
    assert graph.has_recorded_playback("demo") is True
    assert narrated == 1
    paths = graph.flow_step_mouse_paths("demo")
    assert paths[0][0]["x"] == 10
