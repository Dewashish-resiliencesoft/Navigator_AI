"""End-meeting phrases and correction false-positives."""

from navigator.agent.end_policy import is_goodbye
from navigator.agent.nodes.reflecting import classify_correction


def test_end_the_meeting_is_goodbye():
    assert is_goodbye("Okay, end the meeting.")


def test_take_me_to_phonebook_not_correction():
    assert (
        classify_correction(
            "take me to phone book.",
            None,
            complete=lambda _p: "yes",
        )
        is False
    )


def test_404_complaint_not_correction():
    assert (
        classify_correction(
            "then why is it showing 404?",
            None,
            complete=lambda _p: "yes",
        )
        is False
    )
