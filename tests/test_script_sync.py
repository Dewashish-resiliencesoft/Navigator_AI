import yaml

from navigator.knowledge import script_sync as ss
from navigator.knowledge.site_graph import parse_site_graph

INV = [{"label": "Inbox", "href": "/inbox/", "group": ""},
       {"label": "Phonebook", "href": "/phonebook/", "group": ""},
       {"label": "Logout", "href": "/logout/", "group": ""}]

GRAPH = {
    "version": 1, "site": "acme", "base_url": "https://example.test",
    "pages": {"dashboard": {"name": "Dashboard", "url": "/dashboard/",
                            "selectors": {"body": "body", "phonebook": "a[href='/phonebook/']"},
                            "flows": {"tour": [
                                {"tool": "click_element", "selector": "phonebook",
                                 "expects": {"check": "visible", "selector": "body", "timeout_ms": 3000},
                                 "spoken": "Now let's open the Inbox, our team chat."},
                                {"tool": "click_element", "selector": "phonebook",
                                 "expects": {"check": "visible", "selector": "body", "timeout_ms": 3000},
                                 "spoken": "Now the Phonebook with all contacts."},
                                {"tool": "wait_for", "selector": "body", "timeout_ms": 1000,
                                 "expects": {"check": "visible", "selector": "body", "timeout_ms": 1000},
                                 "spoken": "Thanks for watching."}]}}},
}


def test_heuristic_picks_first_named_screen():
    assert ss.heuristic_target("Now let's open the Inbox, then Phonebook", INV)["href"] == "/inbox/"
    assert ss.heuristic_target("Thanks for watching", INV) is None


def test_align_rewrites_only_mismatched_steps_and_keeps_original():
    new, rep = ss.align_flow(yaml.safe_dump(GRAPH), "tour", inventory=INV)
    g = parse_site_graph(new)
    steps = g.pages["dashboard"].flows["tour"]
    assert steps[0].tool == "navigate"                       # said Inbox, clicked Phonebook
    assert g.pages[steps[0].page_id].url == "/inbox/"
    assert steps[1].tool == "click_element"                  # recorded click already matches
    assert steps[2].tool == "wait_for"                       # no screen named -> untouched
    meta = yaml.safe_load(new)["_meta"]
    assert meta["step_sync"]["tour"]["0"]["original"]["selector"] == "phonebook"
    assert all("logout" not in r["href"] for r in rep)


def test_llm_rows_never_highlight_destructive():
    llm = lambda s, u: '[{"i":0,"href":"/inbox/","highlight":"Delete chat"}]'
    rows = ss.llm_targets(["Open the Inbox"], INV, llm)
    assert rows[0]["href"] == "/inbox/" and rows[0]["highlight"] == ""
