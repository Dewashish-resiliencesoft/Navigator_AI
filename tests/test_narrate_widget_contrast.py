"""Narrate Speak/Script language dropdowns must stay readable on light pages."""

from pathlib import Path

_WIDGET = (
    Path(__file__).resolve().parents[1]
    / "navigator"
    / "automation"
    / "narrate_widget.js"
)


def test_narrate_select_forces_dark_option_colors():
    src = _WIDGET.read_text(encoding="utf-8")
    assert "color-scheme: dark" in src
    assert "#nav-narrate select option" in src
    assert "background: #1a2332" in src or "background:#1a2332" in src
