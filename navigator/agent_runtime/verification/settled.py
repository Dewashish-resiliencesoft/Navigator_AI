"""Phase-2: Settled state detection + real semantic verification.

After an action, the browser is not immediately ready. This module answers:
  "Has the DOM settled enough for the next step to begin?"

Checks in order:
  1. No pending navigation / network activity (Playwright waitForLoadState)
  2. No visible loading spinner or skeleton
  3. DOM fingerprint stable across two 150 ms samples
  4. Target element visible (if provided)

Also provides ``verify_step`` — a stronger postcondition than "body visible":
  - url_contains
  - visible element
  - text_contains
  - active navigation item
  - dom_fingerprint change from before→after
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from playwright.sync_api import Error as PlaywrightError, Page

from navigator.agent_runtime.models import DemoStepVerification


_LOADING_SELECTORS = [
    "[data-loading='true']",
    ".loading",
    ".skeleton",
    "[aria-busy='true']",
    ".spinner",
    ".loader",
]

_SETTLE_POLL_MS = 150
_SETTLE_POLLS = 3
_SETTLE_TIMEOUT_MS = 5_000


def _dom_fingerprint(page: Page) -> str:
    try:
        raw = page.evaluate(
            "() => document.body ? document.body.innerHTML.length + '|' + document.querySelectorAll('*').length : '0'"
        )
        return hashlib.md5(str(raw).encode()).hexdigest()[:12]  # noqa: S324
    except PlaywrightError:
        return ""


def _loading_gone(page: Page) -> bool:
    for sel in _LOADING_SELECTORS:
        try:
            if page.query_selector(sel):
                return False
        except PlaywrightError:
            pass
    return True


def wait_settled(page: Page, *, timeout_ms: int = _SETTLE_TIMEOUT_MS) -> bool:
    """Block until the page settles. Returns True if settled within timeout."""
    deadline = time.perf_counter() + timeout_ms / 1000.0
    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 3000))
    except PlaywrightError:
        pass

    prev_fp = ""
    stable_count = 0
    while time.perf_counter() < deadline:
        if not _loading_gone(page):
            time.sleep(_SETTLE_POLL_MS / 1000.0)
            continue
        fp = _dom_fingerprint(page)
        if fp == prev_fp:
            stable_count += 1
            if stable_count >= _SETTLE_POLLS:
                return True
        else:
            stable_count = 0
        prev_fp = fp
        time.sleep(_SETTLE_POLL_MS / 1000.0)
    return False


def verify_step(
    page: Page,
    spec: DemoStepVerification,
    *,
    before_fingerprint: str = "",
) -> tuple[bool, str]:
    """Real state verification — returns (passed, reason).

    Checks any of:
      - url_contains
      - visible element alias/selector
      - text_contains
      - dom fingerprint changed (proving an action had effect)
      - active nav item text
    """
    checks_run: list[str] = []
    any_passed = False

    if spec.url_contains:
        url = page.url
        ok = spec.url_contains in url
        checks_run.append(f"url_contains({spec.url_contains!r})={'✓' if ok else '✗'} [{url}]")
        if ok:
            any_passed = True

    if spec.visible:
        try:
            page.wait_for_selector(spec.visible, state="visible", timeout=2000)
            checks_run.append(f"visible({spec.visible!r})=✓")
            any_passed = True
        except PlaywrightError:
            checks_run.append(f"visible({spec.visible!r})=✗")

    if spec.text_contains:
        try:
            body = page.evaluate("() => document.body ? document.body.innerText : ''")
            ok = spec.text_contains.lower() in body.lower()
            checks_run.append(f"text_contains({spec.text_contains!r})={'✓' if ok else '✗'}")
            if ok:
                any_passed = True
        except PlaywrightError:
            checks_run.append(f"text_contains({spec.text_contains!r})=error")

    if before_fingerprint:
        after_fp = _dom_fingerprint(page)
        changed = before_fingerprint != after_fp
        checks_run.append(f"dom_changed={'✓' if changed else '✗'}")
        if changed:
            any_passed = True

    if spec.active_nav:
        try:
            active_text = page.evaluate(
                """(nav) => {
                    const items = document.querySelectorAll('[aria-current], .active, [data-active="true"]');
                    for (const el of items) {
                        const t = (el.textContent || '').trim().toLowerCase();
                        if (t.includes(nav.toLowerCase())) return t;
                    }
                    return '';
                }""",
                spec.active_nav,
            )
            ok = bool(active_text)
            checks_run.append(f"active_nav({spec.active_nav!r})={'✓' if ok else '✗'}")
            if ok:
                any_passed = True
        except PlaywrightError:
            checks_run.append(f"active_nav({spec.active_nav!r})=error")

    if not checks_run:
        # No spec provided — treat as passed (no constraint)
        return True, "no verification spec"

    return any_passed, "; ".join(checks_run)
