"""Barge-in checker during Meet TTS wait."""

from queue import Queue

from navigator.meeting.barge_in import make_barge_in_checker, pcm_rms


def _loud_pcm(n: int = 3200) -> bytes:
    return (b"\xff\x7f" * (n // 2))[:n]


def test_pcm_rms_silent_is_low():
    assert pcm_rms(b"\x00\x00" * 100) < 1.0


def test_barge_in_stop_word_queues_utterance():
    q: Queue = Queue()
    pending: list[str] = []
    check = make_barge_in_checker(
        q,
        energy_threshold=10.0,
        transcribe=lambda _pcm: "wait hold on a second",
        pending_barge_in=pending,
    )
    q.put(_loud_pcm())
    assert check() is False  # streak 1
    q.put(_loud_pcm())
    assert check() is False  # streak 2
    q.put(_loud_pcm())
    assert check() is True  # streak 3 → STT + stop-word
    assert pending and "wait" in pending[0].lower()


def test_barge_in_ignores_bot_echo():
    q: Queue = Queue()
    pending: list[str] = []
    check = make_barge_in_checker(
        q,
        energy_threshold=10.0,
        is_bot_echo=lambda t: True,
        transcribe=lambda _pcm: "hello from the dashboard",
        pending_barge_in=pending,
    )
    for _ in range(3):
        q.put(_loud_pcm())
        assert check() is False
    assert pending == []
