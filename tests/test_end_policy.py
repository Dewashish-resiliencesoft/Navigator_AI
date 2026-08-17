"""End-policy helpers: goodbye detection and silence D."""

from navigator.agent.end_policy import (
    ANYTHING_ELSE,
    OFF_TOPIC,
    SILENCE_S,
    WRAP_UP,
    is_goodbye,
    next_silence_action,
)


def test_is_goodbye_phrases():
    assert is_goodbye("no thanks")
    assert is_goodbye("that's all, goodbye")
    assert is_goodbye("done")
    assert not is_goodbye("show me contacts")


def test_silence_policy_one_wait_then_leave():
    # First silent 60s after "any questions?" → wrap. No reask round.
    assert next_silence_action(silence_rounds=0) == "leave"
    assert next_silence_action(silence_rounds=1) == "leave"


def test_post_demo_copy_and_timeout():
    assert SILENCE_S == 60.0
    assert "any questions" in ANYTHING_ELSE.lower()
    assert "everything is clear" in WRAP_UP.lower()
    assert "this product" in OFF_TOPIC.lower() or "demo" in OFF_TOPIC.lower()
