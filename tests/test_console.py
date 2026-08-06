"""Windows-safe console helpers."""

from __future__ import annotations

import builtins
import io
import sys

from navigator.core.console import safe_print
from navigator.meeting.intake import _say
from navigator.voice.tts import Speaker


class _RecordingSpeaker:
    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text: str) -> None:
        self.said.append(text)


def test_safe_print_survives_narrow_stdout(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)

    real_print = builtins.print

    def _fail_once(*args, **kwargs):
        if args and "→" in str(args[0]):
            raise UnicodeEncodeError("charmap", "→", 0, 1, "nope")
        return real_print(*args, **kwargs)

    monkeypatch.setattr(builtins, "print", _fail_once)
    safe_print("Hello → world")
    out = buf.getvalue()
    assert "Hello" in out
    assert "world" in out


def test_say_still_speaks_when_log_has_unicode(monkeypatch):
    speaker = _RecordingSpeaker()
    monkeypatch.setattr(
        "navigator.meeting.intake.safe_print",
        lambda _msg: (_ for _ in ()).throw(
            UnicodeEncodeError("charmap", "→", 0, 1, "nope")
        ),
    )
    _say(speaker, "Tailored walkthrough → live demo")
    assert speaker.said == ["Tailored walkthrough → live demo"]
