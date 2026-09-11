"""Playwright browser path fallback when sandbox cache is empty."""

from __future__ import annotations

import os
from pathlib import Path

from navigator.automation.playwright_env import (
    _chromium_present,
    ensure_playwright_browsers,
)


def test_ensure_uses_home_cache_when_sandbox_empty(tmp_path, monkeypatch):
    home = tmp_path / "home"
    bad = tmp_path / "sandbox-empty"
    bad.mkdir()
    good = home / ".cache" / "ms-playwright"
    shell = (
        good
        / "chromium_headless_shell-1234"
        / "chrome-headless-shell-linux64"
        / "chrome-headless-shell"
    )
    shell.parent.mkdir(parents=True)
    shell.write_text("x")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(bad))
    assert not _chromium_present(bad)
    assert _chromium_present(good)
    got = ensure_playwright_browsers()
    assert got == str(good)
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(good)


def test_ensure_keeps_configured_when_populated(tmp_path, monkeypatch):
    root = tmp_path / "pw"
    shell = (
        root
        / "chromium_headless_shell-1"
        / "chrome-headless-shell-linux64"
        / "chrome-headless-shell"
    )
    shell.parent.mkdir(parents=True)
    shell.write_text("x")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "unused-home"))
    assert ensure_playwright_browsers() == str(root)


def test_ensure_headed_display_uses_env(monkeypatch):
    from navigator.automation import playwright_env as pe

    monkeypatch.setenv("DISPLAY", ":99")
    # Only :99 is usable — seats must not steal when they are not reachable.
    monkeypatch.setattr(pe, "_display_usable", lambda d: d == ":99")
    assert pe.ensure_headed_display() == ":99"


def test_ensure_headed_display_prefers_seat_over_xvfb(monkeypatch, tmp_path):
    from navigator.automation import playwright_env as pe

    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(
        pe,
        "_display_usable",
        lambda d: d in {":0", ":99"},
    )

    class FakePath:
        def __init__(self, p):
            self.p = str(p)

        def is_file(self):
            return self.p.endswith(".X0-lock") or self.p.endswith(".X99-lock")

        def exists(self):
            return self.is_file() or "X11-unix" in self.p

    monkeypatch.setattr(pe, "Path", FakePath)
    assert pe.ensure_headed_display() == ":0"
    assert os.environ["DISPLAY"] == ":0"


def test_ensure_headed_display_raises_without_lock(monkeypatch):
    from navigator.automation import playwright_env as pe

    monkeypatch.delenv("DISPLAY", raising=False)

    class FakePath:
        def __init__(self, p):
            self.p = str(p)

        def is_file(self):
            return False

        def exists(self):
            return False

    monkeypatch.setattr(pe, "Path", FakePath)
    try:
        pe.ensure_headed_display()
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "display" in str(exc).lower() or "DISPLAY" in str(exc)
