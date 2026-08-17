"""Intake must ignore bot TTS echo and reserved agent names."""

from navigator.meeting.live_demo import _is_likely_echo


def test_echo_detects_greeting_fragment_against_last_spoken():
    bot = "Hi there, I'm Navigator AI. Thanks for joining — I'll show you ResilioHub live."
    assert _is_likely_echo("Navigator AI", bot)
    assert _is_likely_echo("I'm Navigator AI", bot)
    assert _is_likely_echo("Hi there I'm Navigator AI", bot)


def test_echo_does_not_block_real_name():
    bot = "Hi there, I'm Navigator AI. Thanks for joining."
    assert not _is_likely_echo("Dewashish", bot)
    assert not _is_likely_echo("My name is Priya", bot)


def test_echo_detects_question_replay():
    q = "What would you like ResilioHub to help you with today?"
    assert _is_likely_echo(
        "What would you like ResilioHub to help you with today",
        q,
    )
