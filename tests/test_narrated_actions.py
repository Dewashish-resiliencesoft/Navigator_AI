"""Narrated actions: every click a line claims happens, verified, in order."""
from __future__ import annotations

import pathlib
import threading
import time

import pytest

from navigator.agent import narrated_actions as na

FIX = pathlib.Path(__file__).parent / "fixtures" / "narrated"
pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    import asyncio

    # an earlier test in the full suite leaks a 'running' loop marker; the sync
    # Playwright API refuses to start under it (test-only reset)
    asyncio.events._set_running_loop(None)
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"chromium unavailable: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page(viewport={"width": 1000, "height": 600})
    yield pg
    pg.close()


def _open(page, name):
    page.goto((FIX / name).as_uri())


class Rec:
    def __init__(self, page):
        self.page, self.events = page, []

    def speak(self, text):
        # what is on screen when each segment starts / ends
        self.events.append(("say_start", text, self.page.inner_text("body")))
        time.sleep(0.05)
        self.events.append(("say_end", text, self.page.inner_text("body")))

    def frame(self):
        self.events.append(("frame", "", self.page.inner_text("body")))


def test_parse_inbox_line_claims_a_chat_click():
    t = ("Now let\u2019s open the Inbox. On the left we have all client chats. "
         "Let\u2019s click on a chat to open the conversation on the right.")
    claims = na.parse_claims(t)
    assert [c.target for c in claims] == ["Inbox", "chat"]
    assert claims[1].generic
    assert na.parse_claims("We can click a card to open it.") == []
    assert na.parse_claims("Let's click on Login.", skip_targets=["login"]) == []


def test_inbox_chat_select_opens_conversation(page):
    _open(page, "inbox.html")
    r = Rec(page)
    rest, res = na.run_line(page, "On the left are chats. Let's click on a chat to open the conversation. "
                            "Here we read the full conversation.", speak=r.speak, push_frame=r.frame)
    assert res and res[0].ok and res[0].verified
    assert "Conversation with Asha Rao" in page.inner_text("#conv")
    assert rest == "Here we read the full conversation."
    # the click sentence finished BEFORE the click, and the panel showed when the next segment is due
    assert r.events[1][0] == "say_end" and "Conversation with" not in r.events[1][2]
    assert "Conversation with" in r.events[-1][2]  # final frame shows the opened chat


def test_below_fold_scroll_and_interact_with_sticky_header(page):
    _open(page, "scroll.html")
    res = na.perform(page, na.Claim(0, "click", "Export Report"))
    assert res.ok and res.scrolled and res.container == "page"
    assert "Export panel opened" in page.inner_text("#out")


def test_nested_scroll_panel(page):
    _open(page, "scroll.html")
    res = na.perform(page, na.Claim(0, "click", "Advanced Settings"))
    assert res.ok and res.scrolled and res.container.startswith("div#panel")
    assert "Advanced settings now visible" in page.inner_text("#out2")


def test_failed_click_retries_and_reports_failure(page):
    _open(page, "scroll.html")
    res = na.perform(page, na.Claim(0, "click", "Dead Button"), verify_ms=400)
    assert not res.ok and res.retries == 1 and "no visible change" in res.detail
    r = Rec(page)
    rest, out = na.run_line(page, "Let's click Dead Button. It shows the dead panel. Next, more text.",
                            speak=r.speak)
    assert not out[0].ok
    spoken = " ".join(e[1] for e in r.events if e[0] == "say_start")
    assert "didn't open" in spoken and "dead panel" not in spoken and "dead panel" not in rest


def test_missing_or_unsafe_target_is_not_narrated(page):
    _open(page, "scroll.html")
    r = Rec(page)
    rest, out = na.run_line(page, "Intro. Let's click Fly To Moon. After that.", speak=r.speak)
    spoken = " ".join(e[1] for e in r.events if e[0] == "say_start")
    assert "Fly To Moon" not in spoken and rest == "After that." and not out[0].ok
    assert na.blocked("Delete contact") and na.blocked("Send campaign") and not na.blocked("Create Contact")
    assert not na.perform(page, na.Claim(0, "click", "Locked Feature")).ok


def test_slow_load_waits_for_element(page):
    page.set_content("<body><div id=x>loading</div><script>setTimeout(()=>{x.innerHTML="
                     "'<button onclick=\"this.textContent=\\'Opened the reports view in full\\';"
                     "document.body.append(\\'loaded content that is long enough\\')\">Reports</button>'},700)</script></body>")
    r = Rec(page)
    rest, out = na.run_line(page, "Let's open Reports. These are the reports.", speak=r.speak)
    assert out[0].ok
    assert "Opened the reports" in page.inner_text("body")
    assert rest == "These are the reports."


def test_dialog_scoped_follow_up_click(page):
    _open(page, "scroll.html")
    r = Rec(page)
    rest, out = na.run_line(page, "To add a contact, we click Create Contact. Two options here. "
                            "Let's choose Manual Entry. We enter the name.", speak=r.speak)
    assert out[0].ok and out[0].verified
    assert out[1].strategy.startswith("role=button")
    assert rest == "We enter the name."


def test_already_on_screen_claim_skipped(page):
    _open(page, "inbox.html")
    page.evaluate("history.pushState({}, '', '/inbox/')") if page.url.startswith("http") else None
    c = na.Claim(0, "open", "Inbox")

    class P:
        url = "https://x.test/inbox/"
    assert na.already_there(P(), c)


def test_conditional_sentences_are_descriptions_not_actions():
    assert na.parse_claims("When we open one, it takes us back to the live inbox.") == []
    assert na.parse_claims("If we click Save, it is stored.") == []


def test_unfound_process_description_is_kept_honestly(page):
    page.set_content("<body><p>Send campaign form</p></body>")
    r = Rec(page)
    rest, out = na.run_line(page, "Intro. In step two we choose the campaign type. Done.", speak=r.speak)
    spoken = " ".join(e[1] for e in r.events if e[0] == "say_start")
    assert "you choose the campaign type" in spoken and "we choose" not in spoken
    assert rest == "Done."
