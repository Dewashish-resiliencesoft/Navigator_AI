"""Playlist-only walkthrough: demo flows area drives execution."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from navigator.agent.nodes.listening import _capture_utterance
from navigator.agent.nodes.planning import planning
from navigator.agent.state import CallDeps, CallState, initial_state
from navigator.core.schemas import ClickElement, Postcondition
from navigator.knowledge.site_graph import DemoPlaylistItem, PageSpec, SiteGraph


def _click(alias: str) -> ClickElement:
    return ClickElement(
        tool="click_element",
        selector=alias,
        expects=Postcondition(check="visible", selector=alias, timeout_ms=1000),
    )


@pytest.fixture
def playlist_graph() -> SiteGraph:
    return SiteGraph(
        version=1,
        site="acme",
        base_url="https://app.acme.test/",
        pages={
            "home": PageSpec(
                name="Home",
                url="/",
                selectors={"signup": "text=Sign up"},
                flows={
                    "authentication_flow": (_click("signup"),),
                    "other_flow": (_click("signup"),),
                },
            ),
        },
        demo_playlist=[
            DemoPlaylistItem(
                order=1,
                name="Authentication Flow",
                page_id="home",
                flow_id="authentication_flow",
            ),
        ],
    )


def test_auto_advance_skips_listen_wait(playlist_graph: SiteGraph):
    deps = MagicMock(spec=CallDeps)
    deps.auto_advance_walkthrough = True
    deps.pending_barge_in = []
    deps.spoken_language = "en"
    deps.extra_languages = ("hi",)
    deps.audio_frames = iter([b"\x00" * 6400])
    deps.interactive_listen = False
    state: CallState = {"phase": "walkthrough"}
    assert _capture_utterance(state, deps) == ""


def test_playlist_only_ignores_interrupt_and_runs_next_step(
    playlist_graph: SiteGraph,
):
    speaker = MagicMock()
    speaker.bot_ended = False
    deps = CallDeps(
        graph=playlist_graph,
        page=MagicMock(),
        log=MagicMock(),
        speaker=speaker,
        playlist_only=True,
    )
    state = initial_state(
        __import__("uuid").uuid4(),
        "home",
        walkthrough_flow_id="authentication_flow",
    )
    state["transcript"] = ["user: show me billing"]
    out = planning(state, deps)
    assert out.get("pending_calls")
    assert out["walkthrough_flow_id"] == "authentication_flow"
    assert out["walkthrough_step"] == 1


def test_flow_in_playlist(playlist_graph: SiteGraph):
    assert playlist_graph.flow_in_playlist("home", "authentication_flow")
    assert not playlist_graph.flow_in_playlist("home", "other_flow")
