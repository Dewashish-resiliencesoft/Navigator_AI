"""Recorded flows attach to a real site-graph page, not a phantom dashboard."""

from navigator.client.content import recording_page_id

_YAML = """
version: 1
site: acme
base_url: https://example.com
persona:
  product_name: Acme
  agent_name: Ada
  tone: warm
pages:
  home:
    name: Home
    url: /
    selectors:
      body: body
    flows: {}
demo_playlist: []
"""


def test_recording_page_id_skips_missing_dashboard():
    assert recording_page_id(_YAML, "dashboard") == "home"


def test_recording_page_id_keeps_existing_page():
    assert recording_page_id(_YAML, "home") == "home"
