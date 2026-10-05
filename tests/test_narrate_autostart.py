"""Capture starts the narration mic once; leaving capture stops it."""

from pathlib import Path

_WIDGET = (
    Path(__file__).resolve().parents[1]
    / "navigator"
    / "automation"
    / "narrate_widget.js"
)


def test_capture_starts_mic_once_and_stop_clears_it():
    src = _WIDGET.read_text(encoding="utf-8")
    assert "autoMicForCapture" in src
    assert 'phase === "capturing"' in src
    assert "void startMic()" in src
    assert 'phase === "stopping" || phase === "done"' in src
    assert "if (recording) stopMic()" in src
