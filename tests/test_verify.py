"""Postcondition checking. Every check kind, passing and failing, plus ambiguity."""

from __future__ import annotations

from navigator.browser.verify import check
from navigator.schemas import Postcondition


def verify(page, site_graph, **kwargs):
    kwargs.setdefault("timeout_ms", 700)
    return check(page, site_graph, "inbox", Postcondition(**kwargs))


def test_visible_passes(page, site_graph):
    r = verify(page, site_graph, check="visible", selector="send_button")
    assert r.passed and not r.ambiguous


def test_visible_fails_when_absent(page, site_graph):
    page.evaluate("document.querySelector('#send-btn').remove()")
    r = verify(page, site_graph, check="visible", selector="send_button")
    assert not r.passed
    assert "not found" in r.actual
    assert not r.ambiguous


def test_hidden_passes_for_hidden_element(page, site_graph):
    r = verify(page, site_graph, check="hidden", selector="sent_bubble")
    assert r.passed


def test_hidden_fails_for_visible_element(page, site_graph):
    r = verify(page, site_graph, check="hidden", selector="send_button")
    assert not r.passed
    assert "still visible" in r.actual


def test_text_contains_passes(page, site_graph):
    page.fill("#message-input", "hello world")
    page.click("#send-btn")
    r = verify(
        page,
        site_graph,
        check="text_contains",
        selector="sent_bubble",
        expected="hello",
    )
    assert r.passed
    assert r.actual == "hello world"


def test_text_contains_fails_and_reports_actual(page, site_graph):
    page.fill("#message-input", "hello world")
    page.click("#send-btn")
    r = verify(
        page,
        site_graph,
        check="text_contains",
        selector="sent_bubble",
        expected="goodbye",
    )
    assert not r.passed
    assert r.actual == "hello world", "the failure record needs what was really there"


def test_value_equals_passes(page, site_graph):
    page.fill("#message-input", "exact")
    r = verify(
        page,
        site_graph,
        check="value_equals",
        selector="message_input",
        expected="exact",
    )
    assert r.passed


def test_value_equals_is_exact_not_substring(page, site_graph):
    page.fill("#message-input", "exact plus more")
    r = verify(
        page,
        site_graph,
        check="value_equals",
        selector="message_input",
        expected="exact",
    )
    assert not r.passed
    assert r.actual == "exact plus more"


def test_url_matches_passes(page, site_graph):
    r = verify(page, site_graph, check="url_matches", expected="crm_dashboard.html")
    assert r.passed


def test_url_matches_fails(page, site_graph):
    r = verify(page, site_graph, check="url_matches", expected="/settings")
    assert not r.passed
    assert "crm_dashboard.html" in r.actual


def test_element_count_counts_visible_only(page, site_graph):
    r = verify(page, site_graph, check="element_count", selector="contact_row", expected="3")
    assert r.passed, r.actual

    page.fill("#contact-search", "Priya")
    r = verify(page, site_graph, check="element_count", selector="contact_row", expected="1")
    assert r.passed, r.actual


def test_element_count_fails_with_real_count(page, site_graph):
    r = verify(
        page, site_graph, check="element_count", selector="contact_row", expected="99"
    )
    assert not r.passed
    assert "3 visible" in r.actual


# --- ambiguity: the only path that may escalate to vision --------------------


def test_present_but_invisible_is_ambiguous(page, site_graph):
    """Something is there and we can't tell its state from the DOM alone."""
    page.evaluate(
        "document.querySelector('#send-btn').style.visibility = 'hidden'"
    )
    r = verify(page, site_graph, check="visible", selector="send_button")
    assert not r.passed
    assert r.ambiguous
    assert "never became visible" in r.actual


def test_empty_text_on_visible_element_is_ambiguous(page, site_graph):
    page.evaluate(
        """
        const el = document.createElement('div');
        el.className = 'message sent';
        el.style.height = '10px';
        document.querySelector('#thread').appendChild(el);
        """
    )
    r = verify(
        page, site_graph, check="text_contains", selector="sent_bubble", expected="hi"
    )
    assert not r.passed
    assert r.ambiguous
    assert "no text" in r.actual


def test_absent_element_is_not_ambiguous(page, site_graph):
    """Nothing there is unambiguous. Don't waste a vision call on it."""
    page.evaluate("document.querySelector('#send-btn').remove()")
    r = verify(page, site_graph, check="visible", selector="send_button")
    assert not r.ambiguous


def test_site_graph_error_is_not_ambiguous(page, site_graph):
    r = verify(page, site_graph, check="visible", selector="ghost")
    assert not r.passed
    assert not r.ambiguous
    assert "site graph error" in r.actual
