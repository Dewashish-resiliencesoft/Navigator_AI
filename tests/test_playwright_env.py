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
