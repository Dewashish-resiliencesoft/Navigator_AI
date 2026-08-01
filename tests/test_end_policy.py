"""End-policy helpers: goodbye detection and silence D."""

from navigator.agent.end_policy import is_goodbye, next_silence_action


def test_is_goodbye_phrases():
    assert is_goodbye("no thanks")
    assert is_goodbye("that's all, goodbye")
    assert is_goodbye("done")
    assert not is_goodbye("show me contacts")


def test_silence_policy_d():
    # rounds = how many silent waits already completed
    assert next_silence_action(silence_rounds=0) == "reask"
    assert next_silence_action(silence_rounds=1) == "reask"
    assert next_silence_action(silence_rounds=2) == "leave"
