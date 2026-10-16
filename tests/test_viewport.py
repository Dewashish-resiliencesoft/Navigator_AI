"""One viewport setting for every production browser path."""

from __future__ import annotations

from pathlib import Path

from navigator.automation.browser.viewport import context_kwargs, viewport_px
from navigator.core.settings import settings


def test_viewport_defaults_to_1080p_and_clamps_even(monkeypatch):
    monkeypatch.setattr(settings, "viewport_width", 1920)
    monkeypatch.setattr(settings, "viewport_height", 1080)
    assert viewport_px() == (1920, 1080)
    kw = context_kwargs()
    assert kw == {"viewport": {"width": 1920, "height": 1080}, "device_scale_factor": 1}
    monkeypatch.setattr(settings, "viewport_width", 1921)
    monkeypatch.setattr(settings, "viewport_height", 100)
    assert viewport_px() == (1920, 240)
    monkeypatch.setattr(settings, "viewport_width", 9000)
    monkeypatch.setattr(settings, "viewport_height", 5000)
    assert viewport_px() == (3840, 2160)


def test_production_browser_paths_use_the_shared_viewport():
    root = Path("navigator")
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text()
        rel = path.as_posix()
        if "no_viewport" in text and rel != "navigator/automation/browser/viewport.py":
            offenders.append(f"{rel}: no_viewport")
        if 'viewport={"width": 1280' in text or "viewport={'width': 1280" in text:
            offenders.append(f"{rel}: hard-coded 1280 viewport")
    assert offenders == []
