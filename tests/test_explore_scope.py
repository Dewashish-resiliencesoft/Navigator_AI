"""Explore include/exclude scope filtering."""

from __future__ import annotations

from navigator.automation.explore.session import ExplorationSession


def _el(**kw) -> dict:
    base = {
        "tag": "a", "id": "", "name": "", "testid": "", "text": "",
        "label": "", "aria_label": "", "title": "", "href": "/billing",
    }
    base.update(kw)
    return base


def test_include_paths_blocks_out_of_scope_href():
    session = ExplorationSession(
        product_id="acme",
        base_url="https://app.example.com",
        include_paths=("/contacts",),
    )
    assert session.out_of_scope(_el(href="/billing"), "https://app.example.com/contacts") is not None
    assert session.out_of_scope(_el(href="/contacts/new"), "https://app.example.com/contacts") is None


def test_exclude_labels_skips_matching_controls():
    session = ExplorationSession(
        product_id="acme",
        base_url="https://app.example.com",
        exclude_labels=("logout",),
    )
    reason = session.out_of_scope(_el(text="Logout", href="/"), "https://app.example.com")
    assert reason and "logout" in reason.lower()


def test_exclude_paths_blocks_landing_url():
    session = ExplorationSession(
        product_id="acme",
        base_url="https://app.example.com",
        exclude_paths=("/settings",),
    )
    assert session.path_in_scope("https://app.example.com/settings/profile") is False
    assert session.path_in_scope("https://app.example.com/inbox") is True
