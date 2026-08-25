"""Episode history read-back: proven tactics + known-bad keys.

A past run teaches the next run without a human approving a drafted rule.
Corrupt lines never kill a load. Caps keep the prompt bounded.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import pytest

from navigator.automation.explore.episode import EpisodeStore, StepAttempt
from navigator.automation.explore import history
from navigator.automation.explore.repair import tactics_for
from navigator.automation.explore.reason import choose_next, heuristic_pick


def _attempt(**kw) -> StepAttempt:
    base = dict(
        element_key="testid=save",
        alias="save",
        selector='[data-testid="save"]',
        tool="click_element",
        attempt=0,
        tactic="",
        kind="not_found",
        ok=False,
        detail="waiting for locator",
        duration_ms=1,
        url_before="https://app.example.com/billing/invoices",
        url_after="https://app.example.com/billing/invoices",
    )
    base.update(kw)
    return StepAttempt(**base)


def _write_episode(
    root: Path,
    *,
    product_id: str,
    job_id: str,
    attempts: list[StepAttempt],
) -> EpisodeStore:
    store = EpisodeStore(root=root, product_id=product_id, job_id=job_id)
    for a in attempts:
        store.record(a)
    store.finalize(
        stop_reason=None,
        budget={"max_steps": 10},
        steps=1,
        actions_taken=len(attempts),
    )
    return store


# -- readers ------------------------------------------------------------------


def test_load_recent_newest_first_and_capped(tmp_path):
    root = tmp_path / "ep"
    import os
    import time

    now = time.time()
    for i in range(7):
        _write_episode(
            root,
            product_id="acme",
            job_id=f"j{i}",
            attempts=[_attempt(element_key=f"testid=x{i}")],
        )
        # Distinct recent mtimes so "recent" is deterministic; stay inside the
        # 7-day retention window so creating the next EpisodeStore does not
        # purge earlier fixtures.
        job = root / "acme" / f"j{i}"
        ts = now - (6 - i) * 60  # j0 oldest, j6 newest, all within an hour
        os.utime(job, (ts, ts))

    loaded = history.load_recent(root, "acme", limit=5)
    assert len(loaded) == 5
    assert [e.job_id for e in loaded] == ["j6", "j5", "j4", "j3", "j2"]


def test_known_bad_counts_unrepaired_failures(tmp_path):
    root = tmp_path / "ep"
    # Same key fails unrepaired in two episodes → count 2.
    for job in ("a", "b"):
        _write_episode(
            root,
            product_id="acme",
            job_id=job,
            attempts=[_attempt(element_key="testid=broken", kind="not_found", ok=False)],
        )
    # One unrepaired different key.
    _write_episode(
        root,
        product_id="acme",
        job_id="c",
        attempts=[_attempt(element_key="testid=other", kind="timeout", ok=False)],
    )
    # A repaired failure must NOT count as known-bad.
    _write_episode(
        root,
        product_id="acme",
        job_id="d",
        attempts=[
            _attempt(element_key="testid=fixed", kind="intercepted", ok=False),
            _attempt(
                element_key="testid=fixed",
                attempt=1,
                tactic="dismiss_overlay",
                kind="",
                ok=True,
            ),
        ],
    )

    bad = history.known_bad(root, "acme")
    assert bad["testid=broken"] == ("not_found", 2)
    assert bad["testid=other"] == ("timeout", 1)
    assert "testid=fixed" not in bad


def test_proven_tactics_keyed_by_path_and_kind(tmp_path):
    root = tmp_path / "ep"
    _write_episode(
        root,
        product_id="acme",
        job_id="ok",
        attempts=[
            _attempt(
                kind="not_found",
                ok=False,
                url_before="https://app.example.com/billing/invoices?x=1",
            ),
            _attempt(
                attempt=1,
                tactic="alternate_selector",
                kind="",
                ok=True,
                url_before="https://app.example.com/billing/invoices?x=1",
            ),
        ],
    )
    proven = history.proven_tactics(root, "acme")
    assert proven[("/billing/invoices", "not_found")] == "alternate_selector"


def test_corrupt_jsonl_line_skipped_not_fatal(tmp_path):
    root = tmp_path / "ep"
    store = _write_episode(
        root,
        product_id="acme",
        job_id="messy",
        attempts=[_attempt()],
    )
    with store.attempts_path.open("a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")
        fh.write(
            '{"element_key":"testid=ok2","alias":"ok2","selector":"#x","tool":"click_element",'
            '"attempt":0,"tactic":"","kind":"timeout","ok":false,"detail":"t","duration_ms":1,'
            '"url_before":"https://app.example.com/x","url_after":"https://app.example.com/x"}\n'
        )

    loaded = history.load_recent(root, "acme", limit=5)
    assert len(loaded) == 1
    # Original + the good trailing line; corrupt middle skipped.
    assert len(loaded[0].attempts) >= 2


def test_entry_cap_holds(tmp_path):
    """A long-lived product must not unbounded the prompt."""
    root = tmp_path / "ep"
    attempts = [
        _attempt(element_key=f"testid=e{i}", kind="not_found", ok=False)
        for i in range(history.MAX_ENTRIES + 50)
    ]
    _write_episode(root, product_id="acme", job_id="huge", attempts=attempts)
    bad = history.known_bad(root, "acme")
    assert sum(c for _, c in bad.values()) <= history.MAX_ENTRIES


# -- consumers: repair ladder + reasoner --------------------------------------


def test_tactics_for_tries_proven_first():
    ordered = tactics_for("not_found", proven="alternate_selector")
    assert ordered[0] == "alternate_selector"
    assert "reperceive_refind" in ordered
    # Proven already in the default ladder → not duplicated.
    assert ordered.count("alternate_selector") == 1


def test_tactics_for_ignores_unknown_proven():
    """Don't prepend junk a corrupt history might invent."""
    ordered = tactics_for("timeout", proven="teleport_element")
    assert "teleport_element" not in ordered
    assert ordered[0] == "wait_settle"


def test_choose_next_deprioritizes_known_bad_twice():
    """Keys that already failed unrepaired twice sink below fresh ones."""
    elements = [
        {"tag": "button", "testid": "broken", "text": "Broken", "fillable": False},
        {"tag": "a", "testid": "fresh", "text": "Fresh", "href": "/new", "fillable": False},
    ]
    choice = choose_next(
        url="https://app.example.com/",
        elements=elements,
        known_bad={"testid=broken": 2},
        ask_text=None,
    )
    assert choice is not None
    assert elements[choice.index]["testid"] == "fresh"


def test_choose_next_still_picks_known_bad_when_only_option():
    elements = [
        {"tag": "button", "testid": "broken", "text": "Broken", "fillable": False},
    ]
    choice = choose_next(
        url="https://app.example.com/",
        elements=elements,
        known_bad={"testid=broken": 99},
        ask_text=None,
    )
    assert choice is not None
    assert choice.index == 0


def test_url_path_strips_query():
    assert history.url_path("https://app.example.com/billing?x=1#y") == "/billing"
    assert history.url_path("") == "/"
