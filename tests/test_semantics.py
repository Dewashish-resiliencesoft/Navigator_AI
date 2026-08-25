"""Semantic step labels and flow purposes.

Drives the real `explore()` loop with a fake page and a stub text model, so what
is under test is the shipped control flow. No network, ever: every LLM call is an
injected callable.
"""

from __future__ import annotations

import yaml as _yaml

import pytest

from navigator.agent.nodes.planning import _flow_intent, _flow_texts_for_page
from navigator.automation.explore import semantics
from navigator.automation.explore.explorer import ExplorerDeps, explore
from navigator.automation.explore.session import ExplorationBudget, ExplorationSession
from navigator.automation.record import RecordedStep
from navigator.client.content import merge_recorded_flow
from navigator.core.schemas import ToolResult, VerifyResult
from navigator.knowledge.context import flow_text, score_flows
from navigator.knowledge.site_graph import parse_site_graph


class FakePage:
    """Enough Page for the loop plus `screen_snapshot()`."""

    def __init__(self, url: str, elements: list[dict], text: str = "Dashboard") -> None:
        self.url = url
        self.elements = elements
        self.text = text

    def evaluate(self, _js: str) -> list[dict]:
        return self.elements

    def title(self) -> str:
        return "Acme"

    def inner_text(self, _sel: str, timeout: int = 1500) -> str:
        return self.text

    def screenshot(self, **_kw) -> bytes:
        return b"\xff\xd8\xfffakejpeg"

    def wait_for_timeout(self, _ms: int) -> None:
        return None

    def go_back(self, timeout: int = 8000) -> None:
        raise RuntimeError("no history")


def _el(**kw) -> dict:
    base = {
        "tag": "button", "id": "", "name": "", "testid": "", "text": "",
        "label": "", "aria_label": "", "title": "", "alt": "", "role": "",
        "type": "", "autocomplete": "", "href": "", "value": "", "fillable": False,
    }
    base.update(kw)
    return base


def _snap(url: str = "https://app.example.com/", text: str = "before") -> semantics.StateSnapshot:
    return semantics.StateSnapshot(url=url, title="Acme", text_hash=text, text=text)


# -- diff detection -----------------------------------------------------------


def test_spinner_only_change_is_not_meaningful():
    """A loading indicator arriving is the page working, not an achievement."""
    before = _snap(text="same")
    after = _snap(text="same")
    diff = semantics.diff_summary(
        before,
        after,
        [_el(testid="go", text="Go")],
        [_el(testid="go", text="Go"), _el(testid="spin", text="Loading…", role="progressbar")],
    )
    assert diff.has_meaningful_change is False


def test_new_form_is_meaningful():
    diff = semantics.diff_summary(
        _snap(text="a"),
        _snap(text="b"),
        [_el(testid="create", text="Create")],
        [_el(testid="create", text="Create"), _el(testid="name", text="Campaign name")],
    )
    assert diff.has_meaningful_change is True
    assert "Added" in diff.summary


def test_url_change_is_meaningful_even_with_same_elements():
    els = [_el(testid="x", text="X")]
    diff = semantics.diff_summary(
        _snap(url="https://app.example.com/"),
        _snap(url="https://app.example.com/billing"),
        els,
        els,
    )
    assert diff.has_meaningful_change is True
    assert diff.url_changed is True


def test_no_change_at_all_is_not_meaningful():
    els = [_el(testid="x", text="X")]
    snap = _snap()
    assert semantics.diff_summary(snap, snap, els, els).has_meaningful_change is False


# -- label_step ---------------------------------------------------------------


def _diff_meaningful() -> semantics.StateDiff:
    return semantics.StateDiff(
        added=("form: New Invoice",), url_changed=True, has_meaningful_change=True
    )


def test_label_step_returns_empty_on_provider_failure():
    def boom(_prompt: str) -> str:
        raise RuntimeError("groq down")

    assert semantics.label_step(
        tool="click_element", element="button: Create",
        before=_snap(), after=_snap(), diff=_diff_meaningful(), ask_text=boom,
    ) == ""


def test_label_step_returns_empty_without_model():
    assert semantics.label_step(
        tool="click_element", element="b",
        before=_snap(), after=_snap(), diff=_diff_meaningful(), ask_text=None,
    ) == ""


def test_label_step_honours_no_change_sentinel():
    assert semantics.label_step(
        tool="click_element", element="b", before=_snap(), after=_snap(),
        diff=_diff_meaningful(), ask_text=lambda _p: "NO_CHANGE",
    ) == ""


def test_label_step_not_called_when_diff_is_meaningless():
    """No spend on a step that achieved nothing."""
    calls: list[str] = []

    def spy(prompt: str) -> str:
        calls.append(prompt)
        return "Opens something"

    out = semantics.label_step(
        tool="click_element", element="b", before=_snap(), after=_snap(),
        diff=semantics.StateDiff(has_meaningful_change=False), ask_text=spy,
    )
    assert out == ""
    assert calls == [], "must not call the model when nothing changed"


def test_label_step_truncates_to_fifteen_words():
    long = " ".join(f"w{i}" for i in range(40))
    out = semantics.label_step(
        tool="click_element", element="b", before=_snap(), after=_snap(),
        diff=_diff_meaningful(), ask_text=lambda _p: long,
    )
    assert len(out.split()) == 15


def test_label_step_strips_quotes_and_extra_lines():
    out = semantics.label_step(
        tool="click_element", element="b", before=_snap(), after=_snap(),
        diff=_diff_meaningful(),
        ask_text=lambda _p: '"Opens the invoice form"\nsome trailing junk',
    )
    assert out == "Opens the invoice form"


# -- label_flow ---------------------------------------------------------------


def test_label_flow_parses_json_and_lowercases_tags():
    sem = semantics.label_flow(
        ["Opens the invoice form", "Sends the invoice"],
        ask_text=lambda _p: (
            'Here you go: {"name": "Create Invoice", '
            '"purpose": "Creates and sends an invoice", "tags": ["Billing", "INVOICE"]}'
        ),
    )
    assert sem.auto_name == "Create Invoice"
    assert sem.purpose == "Creates and sends an invoice"
    assert sem.tags == ("billing", "invoice")


def test_label_flow_empty_on_unparseable_reply():
    sem = semantics.label_flow(["a step"], ask_text=lambda _p: "no json here")
    assert sem.purpose == "" and sem.tags == ()


def test_label_flow_skips_when_all_labels_blank():
    calls: list[str] = []
    sem = semantics.label_flow(["", "   "], ask_text=lambda p: calls.append(p) or "{}")
    assert sem.purpose == ""
    assert calls == []


# -- integration through the real explore() loop ------------------------------


def _run_explore(*, label_ask, page=None) -> ExplorationSession:
    el = _el(testid="billing-nav", text="Billing", tag="a", href="/billing")
    page = page or FakePage("https://app.example.com/", [el])

    def _execute(_p, _graph, _page_id, call):
        page.url = "https://app.example.com/billing"
        page.elements = [_el(testid="invoice", text="Invoices")]
        page.text = "Invoices list"
        return ToolResult(ok=True, tool=call.tool, detail="ok", duration_ms=1), "main"

    session = ExplorationSession(
        product_id="acme",
        base_url="https://app.example.com",
        budget=ExplorationBudget(max_steps=2, max_pages=3),
    )
    explore(
        session,
        ExplorerDeps(
            page=page,
            execute=_execute,
            verify=lambda *_a: VerifyResult(passed=True, actual="ok"),
            guard_judge=lambda _p: '{"destructive": false}',
            label_ask=label_ask,
        ),
    )
    return session


def test_labels_align_with_steps_by_index():
    session = _run_explore(label_ask=lambda _p: "Opens the billing page")
    assert session.steps
    assert len(session.step_labels) == len(session.steps)
    assert session.step_labels[0] == "Opens the billing page"


def test_run_completes_when_labelling_raises():
    """A dead label model must not take the exploration down with it."""

    def boom(_prompt: str) -> str:
        raise RuntimeError("provider exploded")

    session = _run_explore(label_ask=boom)
    assert session.steps, "steps still captured"
    assert session.step_labels == [""], "label absent, alignment preserved"


def test_labelling_disabled_keeps_alignment():
    session = _run_explore(label_ask=None)
    assert len(session.step_labels) == len(session.steps)
    assert all(label == "" for label in session.step_labels)


# -- persistence: _meta survives the YAML round-trip --------------------------

_BASE_YAML = """
version: 1
site: acme
base_url: https://app.example.com
pages:
  main:
    name: Main
    url: /
    selectors: {btn: "#b"}
    flows:
      explored_a1b2:
        - tool: click_element
          selector: btn
          expects: {check: visible, selector: btn}
demo_playlist:
  - {order: 1, name: Explored, page_id: main, flow_id: explored_a1b2}
_meta:
  semantics:
    explored_a1b2:
      purpose: "Create and send an invoice"
      tags: [billing, invoice]
      steps:
        - {idx: 0, description: "Opens the invoice form"}
"""


def test_meta_semantics_survive_merge_recorded_flow():
    out = merge_recorded_flow(
        _BASE_YAML,
        flow_name="Another",
        flow_id="explored_z9y8",
        page_id="main",
        steps=[RecordedStep(tool="click_element", alias="btn2", selector="#b2")],
        product_name="Acme",
        base_url="https://app.example.com",
    )
    sem = _yaml.safe_load(out)["_meta"]["semantics"]["explored_a1b2"]
    assert sem["purpose"] == "Create and send an invoice"
    assert sem["steps"][0]["description"] == "Opens the invoice form"


def test_parse_site_graph_exposes_meta_and_tolerates_absence():
    graph = parse_site_graph(_BASE_YAML)
    assert graph.flow_semantics("explored_a1b2")["purpose"] == "Create and send an invoice"
    assert graph.flow_semantics("no_such_flow") == {}

    bare = parse_site_graph(_BASE_YAML.split("_meta:")[0])
    assert bare.meta == {}
    assert bare.flow_semantics("explored_a1b2") == {}


def test_malformed_meta_degrades_to_empty():
    """A stale or model-mangled _meta must not fail an otherwise valid graph."""
    bad = _BASE_YAML.replace(
        "  semantics:\n    explored_a1b2:", "  semantics: not-a-mapping\n  ignored:"
    )
    graph = parse_site_graph(bad.split("      purpose")[0])
    assert graph.flow_semantics("explored_a1b2") == {}


# -- B4: purposes actually change ranking -------------------------------------


def test_purpose_beats_slug_only_ranking():
    """The point of the whole phase: an opaque flow id ranks by luck."""
    query = "how do I bill a customer"
    ids = ["explored_a1b2", "explored_z9y8"]

    slug_only = {f: flow_text(f) for f in ids}
    scored = dict(score_flows(query, slug_only))
    assert scored["explored_a1b2"] == pytest.approx(scored["explored_z9y8"], abs=0.05), (
        "slug-only ids carry no signal, so neither should win"
    )

    with_purpose = {
        "explored_a1b2": flow_text(
            "explored_a1b2",
            trigger_intent="Create and send an invoice to a customer — billing invoice",
        ),
        "explored_z9y8": flow_text(
            "explored_z9y8",
            trigger_intent="View the analytics dashboard — analytics charts",
        ),
    }
    ranked = score_flows(query, with_purpose)
    assert ranked[0][0] == "explored_a1b2"
    assert ranked[0][1] > ranked[1][1] + 0.1


class _FakeDeps:
    """Minimal CallDeps stand-in: `_flow_texts_for_page` only touches `graph`."""

    def __init__(self, graph) -> None:
        self.graph = graph


def test_flow_texts_for_page_includes_generated_purpose():
    deps = _FakeDeps(parse_site_graph(_BASE_YAML))
    texts = _flow_texts_for_page(deps, "main")
    assert "invoice" in texts["explored_a1b2"].lower()
    assert "billing" in texts["explored_a1b2"].lower()


def test_flow_intent_empty_without_semantics():
    deps = _FakeDeps(parse_site_graph(_BASE_YAML.split("_meta:")[0]))
    assert _flow_intent(deps, "explored_a1b2") == ""
    texts = _flow_texts_for_page(deps, "main")
    assert texts["explored_a1b2"], "still falls back to id + playlist name"
