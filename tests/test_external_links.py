"""Off-product link detection and demo disclaimer."""

from __future__ import annotations

from navigator.automation.external_links import (
    EXTERNAL_LINK_SPOKEN,
    element_is_external,
    is_external_href,
    is_external_url,
)
from navigator.automation.explore.reason import choose_next


def test_is_external_href_same_origin_relative():
    base = "https://app.example.com/dashboard"
    assert not is_external_href("/billing", base, page_url=base)
    assert not is_external_href("billing", base, page_url=base)


def test_is_external_href_other_origin():
    base = "https://app.example.com/"
    assert is_external_href("https://docs.google.com/x", base)
    assert is_external_href("//cdn.example.net/x", base, page_url=base)


def test_mailto_and_tel_are_external():
    base = "https://app.example.com/"
    assert is_external_href("mailto:support@acme.com", base)
    assert is_external_href("tel:+15551212", base)


def test_element_is_external_new_tab():
    el = {
        "href": "https://other.com/help",
        "target": "_blank",
        "tag": "a",
    }
    assert element_is_external(el, "https://app.example.com/")


def test_choose_next_prefers_in_product_link():
    elements = [
        {
            "tag": "a",
            "testid": "ext",
            "text": "Help center",
            "href": "https://help.other.com/",
            "fillable": False,
        },
        {
            "tag": "a",
            "testid": "home",
            "text": "Home",
            "href": "/home",
            "fillable": False,
        },
    ]
    choice = choose_next(
        url="https://app.example.com/app",
        elements=elements,
        product_base="https://app.example.com",
        ask_text=None,
    )
    assert choice is not None
    assert choice.index == 1


def test_is_external_url():
    assert is_external_url("https://evil.com/x", "https://app.example.com/")
    assert not is_external_url(
        "https://app.example.com/billing", "https://app.example.com/"
    )


def test_demo_disclaimer_copy_mentions_own():
    assert "your own" in EXTERNAL_LINK_SPOKEN.lower()
    assert "not part" in EXTERNAL_LINK_SPOKEN.lower()
