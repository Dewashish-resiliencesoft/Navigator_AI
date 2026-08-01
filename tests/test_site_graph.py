"""A bad site graph must fail at load, never mid-call. These are those failures."""

from __future__ import annotations

import textwrap

import pytest
from pydantic import ValidationError

from navigator.knowledge.site_graph import SiteGraphError, load_site_graph
from navigator.core.schemas import Postcondition


def write_graph(tmp_path, body: str):
    path = tmp_path / "graph.yaml"
    path.write_text(textwrap.dedent(body))
    return path


BASE = """
version: 1
site: test
base_url: https://example.test/
pages:
  home:
    name: Home
    url: index.html
    selectors:
      button: "#btn"
      banner: ".banner"
"""


def test_loads_seed_graph(site_graph):
    assert site_graph.site == "whatsapp-crm"
    assert site_graph.version == 1
    assert "inbox" in site_graph.pages
    assert site_graph.selector("inbox", "send_button") == "#send-btn"


def test_seed_graph_url_resolves_to_fixture(site_graph):
    url = site_graph.url_for("inbox")
    assert url.startswith("file://")
    assert url.endswith("tests/fixtures/crm_dashboard.html")


def test_seed_graph_flow_parses_into_tool_calls(site_graph):
    steps = site_graph.flow("inbox", "send_test_message")
    assert [c.tool for c in steps] == [
        "navigate",
        "wait_for",
        "fill_field",
        "click_element",
    ]
    assert steps[2].value == "Hi from Navigator AI"
    assert steps[2].source == "agent"


def test_unknown_page_raises(site_graph):
    with pytest.raises(SiteGraphError, match="unknown page 'nope'"):
        site_graph.page("nope")


def test_unknown_selector_raises(site_graph):
    with pytest.raises(SiteGraphError, match="no selector 'nope'"):
        site_graph.selector("inbox", "nope")


def test_unknown_flow_raises(site_graph):
    with pytest.raises(SiteGraphError, match="no flow 'nope'"):
        site_graph.flow("inbox", "nope")


# --- the four cross-checks ---------------------------------------------------


def test_rejects_tool_call_with_unknown_selector(tmp_path):
    path = write_graph(
        tmp_path,
        BASE
        + """
    flows:
      f:
        - tool: click_element
          selector: ghost
          expects: {check: visible, selector: banner}
""",
    )
    with pytest.raises(SiteGraphError, match="unknown selector 'ghost'"):
        load_site_graph(path)


def test_rejects_postcondition_with_unknown_selector(tmp_path):
    path = write_graph(
        tmp_path,
        BASE
        + """
    flows:
      f:
        - tool: click_element
          selector: button
          expects: {check: visible, selector: ghost}
""",
    )
    with pytest.raises(SiteGraphError, match="postcondition targets unknown selector"):
        load_site_graph(path)


def test_rejects_navigate_to_unknown_page(tmp_path):
    path = write_graph(
        tmp_path,
        BASE
        + """
    flows:
      f:
        - tool: navigate
          page_id: elsewhere
          expects: {check: url_matches, expected: elsewhere}
""",
    )
    with pytest.raises(SiteGraphError, match="unknown page 'elsewhere'"):
        load_site_graph(path)


def test_rejects_postcondition_missing_selector(tmp_path):
    """Only url_matches may omit a selector; Postcondition itself enforces that."""
    path = write_graph(
        tmp_path,
        BASE
        + """
    flows:
      f:
        - tool: click_element
          selector: button
          expects: {check: visible}
""",
    )
    with pytest.raises(SiteGraphError, match="visible requires `selector`"):
        load_site_graph(path)


def test_navigate_postcondition_resolves_against_destination_page(tmp_path):
    """`away.only_there` doesn't exist on `home`, but navigate lands on `away`."""
    path = write_graph(
        tmp_path,
        BASE
        + """
    flows:
      f:
        - tool: navigate
          page_id: away
          expects: {check: visible, selector: only_there}
  away:
    name: Away
    url: away.html
    selectors:
      only_there: "#only-there"
""",
    )
    graph = load_site_graph(path)
    assert graph.flow("home", "f")[0].page_id == "away"


# --- other load failures -----------------------------------------------------


def test_missing_file_raises(tmp_path):
    with pytest.raises(SiteGraphError, match="not found"):
        load_site_graph(tmp_path / "absent.yaml")


def test_invalid_yaml_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("version: 1\n  bad indent: [")
    with pytest.raises(SiteGraphError, match="invalid YAML"):
        load_site_graph(path)


def test_non_mapping_raises(tmp_path):
    path = tmp_path / "list.yaml"
    path.write_text("- a\n- b\n")
    with pytest.raises(SiteGraphError, match="expected a mapping"):
        load_site_graph(path)


def test_unknown_tool_rejected(tmp_path):
    path = write_graph(
        tmp_path,
        BASE
        + """
    flows:
      f:
        - tool: hack_the_dom
          expects: {check: visible, selector: button}
""",
    )
    with pytest.raises(SiteGraphError):
        load_site_graph(path)


# --- Postcondition shape rules ----------------------------------------------


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"check": "text_contains", "selector": "x"}, "requires `expected`"),
        ({"check": "value_equals", "selector": "x"}, "requires `expected`"),
        ({"check": "url_matches"}, "requires `expected`"),
        ({"check": "element_count", "selector": "x"}, "requires `expected`"),
        (
            {"check": "element_count", "selector": "x", "expected": "many"},
            "expects an integer",
        ),
        ({"check": "hidden"}, "requires `selector`"),
        ({"check": "visible", "selector": "x", "timeout_ms": 0}, "greater than 0"),
    ],
)
def test_postcondition_rejects_bad_shapes(kwargs, match):
    with pytest.raises(ValidationError, match=match):
        Postcondition(**kwargs)


def test_url_matches_needs_no_selector():
    assert Postcondition(check="url_matches", expected="/inbox").selector is None
