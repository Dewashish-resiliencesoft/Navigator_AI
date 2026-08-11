"""SPEAKING routes through the Live session when one is attached."""

from __future__ import annotations

from dataclasses import replace

from navigator.agent.nodes.speaking import _say_mode, speaking


class FakeLive:
    def __init__(self, *, interrupt_on: str | None = None) -> None:
        self.said: list[tuple[str, str]] = []
        self.context: list[str] = []
        self.interrupted = False
        self.idle_waits = 0
        self._interrupt_on = interrupt_on

    def say(self, text: str, *, mode: str = "verbatim") -> None:
        self.said.append((text, mode))
        self.interrupted = text == self._interrupt_on

    def add_context(self, text: str) -> None:
        self.context.append(text)

    def wait_until_idle(self, *, silence_s: float, timeout_s: float = 30.0) -> None:
        self.idle_waits += 1


def test_live_agent_takes_over_from_the_tts_speaker(state, deps):
    live = FakeLive()
    deps = replace(deps, live_agent=live)
    state["narration"] = ["one", "two"]

    speaking(state, deps)

    assert [t for t, _ in live.said] == ["one", "two"]
    assert deps.speaker.said == [], "MeetSpeaker must not also speak"


def test_tts_path_untouched_without_a_live_agent(state, deps):
    state["narration"] = ["one"]
    speaking(state, deps)
    assert deps.speaker.said == ["one"]


def test_interruption_stops_the_queue_and_waits_for_quiet(state, deps):
    live = FakeLive(interrupt_on="one")
    deps = replace(deps, live_agent=live)
    state["narration"] = ["one", "two"]

    speaking(state, deps)

    assert [t for t, _ in live.said] == ["one"], "must not talk over the prospect"
    assert live.idle_waits == 1


def test_client_authored_lines_are_spoken_verbatim(deps):
    authored = deps.graph.flow_narration_lines("send_test_message")
    if not authored:
        return  # fixture graph has no authored narration
    assert _say_mode(deps, authored[0]) == "verbatim"


def test_runtime_phrased_lines_may_be_paraphrased(deps):
    assert _say_mode(deps, "a line nobody wrote into the site graph") == "natural"


def test_live_skips_planner_replay_after_user_question(state, deps):
    live = FakeLive()
    deps = replace(deps, live_agent=live)
    state["transcript"] = ["user: what does that button do?"]
    state["narration"] = ["That button sends the message."]
    state["pending_calls"] = []

    speaking(state, deps)

    assert live.said == []
    assert deps.speaker.said == []
