import yaml
from navigator.knowledge import flow_authoring as fa

Y = yaml.safe_dump({"version": 1, "site": "x", "base_url": "https://x.test", "pages": {
    "home": {"url": "/", "flows": {"tour": [
        {"tool": "click_element", "selector": "inbox", "expects": {"check": "visible", "selector": "body"}},
        {"tool": "click_element", "selector": "phonebook", "expects": {"check": "visible", "selector": "body"}},
        {"tool": "click_element", "selector": "campaigns", "expects": {"check": "visible", "selector": "body"}}]}}}})
STEPS = [{"label": "Inbox", "old": "a b c"}, {"label": "Phonebook", "old": "a b c"}, {"label": "Campaigns", "old": "a"}]


def test_step_name_roundtrip_keeps_selector():
    y = fa.set_step_name(Y, flow_id="tour", step_index=1, name="Open contacts")
    assert fa.step_names(y)["tour"][1] == "Open contacts"
    assert yaml.safe_load(y)["pages"]["home"]["flows"]["tour"][1]["selector"] == "phonebook"


def test_step_name_bad_index():
    import pytest
    with pytest.raises(ValueError):
        fa.set_step_name(Y, flow_id="tour", step_index=9, name="x")


def test_distribute_markers_numbers_and_names():
    t = "Step 1: Chats live here.\n2. All contacts.\nCampaigns: Bulk sends."
    lines, m = fa.distribute_script(t, STEPS)
    assert m == "markers" and lines == ["Chats live here.", "All contacts.", "Bulk sends."]


def test_distribute_paragraphs():
    lines, m = fa.distribute_script("One.\n\nTwo.\n\nThree.", STEPS)
    assert m == "paragraphs" and lines == ["One.", "Two.", "Three."]


def test_distribute_llm_then_proportional():
    lines, m = fa.distribute_script("A one. B two. C three. D four.", STEPS,
                                    llm=lambda s, u: '["A one. B two.", "C three.", "D four."]')
    assert m == "llm" and lines[2] == "D four."
    lines, m = fa.distribute_script("A one. B two. C three. D four.", STEPS, llm=None)
    assert m == "proportional" and " ".join(x for x in lines if x) == "A one. B two. C three. D four."


def test_add_ai_flow_is_new_flow_and_playlist_draft():
    draft = {"name": "Quick tour", "start_path": "/", "steps": [{"label": "Inbox", "name": "Inbox", "narration": "Chats."}]}
    y, fid, pid = fa.add_ai_flow_to_yaml(Y, draft)
    raw = yaml.safe_load(y)
    assert fid.startswith("ai_") and pid == "home" and "tour" in raw["pages"]["home"]["flows"]
    assert raw["pages"]["home"]["flows"][fid][0]["selector"] == 'text="Inbox"'
    assert raw["_meta"]["ai_drafts"][fid]["needs_review"] is True


def test_plan_tour_fallback_skips_destructive():
    p = fa.plan_tour(["Inbox", "Logout", "Phonebook", "Sign in"], "", "x", llm=None)
    assert [s["control"] for s in p["steps"]] == ["Inbox", "Phonebook"]


# ---- step names from segments, structural edits, extras

_G2 = """
version: 1
site: t
base_url: https://example.test
pages:
  dashboard:
    name: Dashboard
    url: /dashboard
    selectors: {login: '#l', inbox: '#i', body: body}
    flows:
      f:
      - {tool: click_element, selector: login, spoken: one, expects: {check: visible, selector: body, timeout_ms: 3000}}
      - {tool: click_element, selector: inbox, spoken: two, expects: {check: visible, selector: body, timeout_ms: 3000}}
_meta:
  step_names: {f: {1: Inbox}}
  step_timing: {f: [{idx: 0, speak_ms: 1}, {idx: 1, speak_ms: 2}]}
  narration_suggestions: {f: [s0, s1]}
"""


def test_extract_segments_names():
    from navigator.knowledge.flow_authoring import extract_segments

    segs = extract_segments("Step 1: Login — sign in here.\nStep 2: Inbox — all chats.\n## Settings\nChange prefs.")
    assert [s["name"] for s in segs] == ["Login", "Inbox", "Settings"]
    assert segs[2]["text"] == "Change prefs."
    assert extract_segments("1. Welcome to the dashboard.")[0]["name"] is None


def test_propose_names_llm_and_heuristic():
    from navigator.knowledge.flow_authoring import propose_step_names

    assert propose_step_names(["the inbox shows client chats"], lambda s, u: '["Inbox overview"]') == ["Inbox overview"]
    h = propose_step_names(["Here we open the inbox screen with client chats"], None)[0]
    assert 1 <= len(h.split()) <= 4 and "Inbox" in h or "inbox" in h.lower()


def test_insert_delete_move_reindex():
    import yaml
    from navigator.knowledge.flow_authoring import edit_flow_steps
    from navigator.knowledge.site_graph import parse_site_graph

    y, focus = edit_flow_steps(_G2, flow_id="f", op="insert", index=0,
                               spec={"kind": "click", "value": "Reports", "narration": "see reports", "name": "Reports"})
    assert focus == 1
    raw = yaml.safe_load(y)
    st = raw["pages"]["dashboard"]["flows"]["f"]
    assert raw["pages"]["dashboard"]["selectors"][st[1]["selector"]] == 'text="Reports"' and st[1]["spoken"] == "see reports"
    assert raw["_meta"]["step_names"]["f"] == {2: "Inbox", 1: "Reports"}
    assert raw["_meta"]["narration_suggestions"]["f"] == ["s0", "", "s1"]
    assert "f" not in raw["_meta"]["step_timing"]
    g = parse_site_graph(y)
    assert g.selector("dashboard", st[1]["selector"]) == 'text="Reports"'
    y, _ = edit_flow_steps(y, flow_id="f", op="up", index=2)
    assert yaml.safe_load(y)["_meta"]["step_names"]["f"] == {1: "Inbox", 2: "Reports"}
    y, _ = edit_flow_steps(y, flow_id="f", op="delete", index=1)
    raw = yaml.safe_load(y)
    assert [s.get("spoken") for s in raw["pages"]["dashboard"]["flows"]["f"]] == ["one", "see reports"]
    assert raw["_meta"]["step_names"]["f"] == {1: "Reports"}


def test_navigate_and_narration_steps_parse():
    import yaml
    from navigator.knowledge.flow_authoring import append_narration_steps, edit_flow_steps, step_kind
    from navigator.knowledge.site_graph import parse_site_graph

    y, _ = edit_flow_steps(_G2, flow_id="f", op="insert", index=-1, spec={"kind": "navigate", "value": "https://example.test/dashboard"})
    y, _ = edit_flow_steps(y, flow_id="f", op="insert", index=0, spec={"kind": "navigate", "value": "https://example.test/reports"})
    y = append_narration_steps(y, flow_id="f", extras=[{"name": "Wrap up", "text": "Thanks!"}])
    parse_site_graph(y)
    raw = yaml.safe_load(y)
    st = raw["pages"]["dashboard"]["flows"]["f"]
    assert st[0]["page_id"] == "dashboard" and st[1]["page_id"] == "page_reports"
    assert step_kind(st[-1]) == ("narration", "") and st[-1]["spoken"] == "Thanks!"
    assert raw["_meta"]["step_names"]["f"][len(st) - 1] == "Wrap up"


def test_delete_last_step_refused():
    import pytest
    from navigator.knowledge.flow_authoring import edit_flow_steps

    y, _ = edit_flow_steps(_G2, flow_id="f", op="delete", index=0)
    with pytest.raises(ValueError):
        edit_flow_steps(y, flow_id="f", op="delete", index=0)


def test_narration_wait_for_body_is_noop():
    from navigator.automation.browser.tools import wait_for
    from navigator.core.schemas import WaitFor
    from navigator.knowledge.site_graph import parse_site_graph

    call = WaitFor(selector="body", timeout_ms=1000, expects={"check": "visible", "selector": "body", "timeout_ms": 1000})
    assert wait_for(None, parse_site_graph(_G2), "dashboard", call)[0] == "body ready"


def test_title_line_is_name_not_narration():
    from navigator.knowledge.flow_authoring import _by_markers, extract_segments, strip_leading_title

    segs = extract_segments("Step 1: Login\nHere we sign in.\nStep 2: Dashboard\nThis is the dashboard.")
    assert segs == [{"name": "Login", "text": "Here we sign in."}, {"name": "Dashboard", "text": "This is the dashboard."}]
    assert _by_markers("Step 1: Login\nHere we sign in.\nStep 2: Inbox\nAll chats.", [{"label": "a"}, {"label": "b"}]) == ["Here we sign in.", "All chats."]
    assert strip_leading_title("History In History we search past chats.") == ("History", "In History we search past chats.")
    assert strip_leading_title("Hello, this is the landing page.")[0] == ""
    assert strip_leading_title("Now we open the Inbox and see chats.")[0] == ""


def test_scripted_say_refuses_tool_calls():
    import asyncio
    from types import SimpleNamespace
    from navigator.voice.live_agent import LiveAgent

    sent = {}
    agent = LiveAgent.__new__(LiveAgent)
    agent._in_say = True
    agent.cfg = SimpleNamespace(tool_handler=lambda n, a: (_ for _ in ()).throw(AssertionError("must not run")))

    class _S:
        async def send_tool_response(self, function_responses):
            sent["r"] = function_responses
    agent._session = _S()
    agent._emit = lambda e: None
    asyncio.run(agent._run_tools([SimpleNamespace(name="show_feature", args={"target": "Inbox"}, id="1")]))
    assert sent["r"][0].response["ok"] is False
