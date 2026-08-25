"""Auto Product Map from explored flow semantics."""

from __future__ import annotations

from navigator.app.registry import Registry, NewProduct
from navigator.automation.explore import product_areas
from navigator.knowledge.context import retrieve_context
from navigator.knowledge.product_map import ProductMapStore


_YAML = """
version: 1
site: acme
base_url: https://app.example.com
pages:
  main:
    name: Main
    url: /
    selectors: {body: body}
    flows:
      explored_bill:
        - tool: wait_for
          selector: body
          expects: {check: visible, selector: body}
      explored_analytics:
        - tool: wait_for
          selector: body
          expects: {check: visible, selector: body}
demo_playlist:
  - {order: 1, name: Invoice, page_id: main, flow_id: explored_bill}
  - {order: 2, name: Charts, page_id: main, flow_id: explored_analytics}
_meta:
  semantics:
    explored_bill:
      purpose: "Create and send an invoice"
      tags: [billing, invoice, create]
      auto_name: "Create Invoice"
    explored_analytics:
      purpose: "View the analytics dashboard"
      tags: [analytics, charts]
      auto_name: "View Analytics"
"""


def test_sync_groups_by_tag(tmp_path):
    registry = Registry(tmp_path / "reg.db")
    registry.register(NewProduct(name="Acme", product_id="acme"))
    written = product_areas.sync_from_yaml(registry, "acme", _YAML, product_name="Acme")
    assert len(written) >= 2
    store = ProductMapStore(registry._conn)
    areas = {a.area_id: a for a in store.list_product("acme")}
    assert "billing" in areas or any("invoice" in a.related_flow_ids[0] for a in areas.values())
    bill = next(a for a in areas.values() if "explored_bill" in a.related_flow_ids)
    assert "invoice" in bill.purpose.lower() or "billing" in bill.categories


def test_retrieve_context_populates_relevant_areas(tmp_path):
    registry = Registry(tmp_path / "reg.db")
    registry.register(NewProduct(name="Acme", product_id="acme"))
    product_areas.sync_from_yaml(registry, "acme", _YAML, product_name="Acme")

    result = retrieve_context(
        "how do I bill a customer",
        "acme",
        available_flow_ids=["explored_bill", "explored_analytics"],
        registry=registry,
        chroma_path=tmp_path / "chroma",
    )
    assert result.relevant_areas, "areas must surface once the map exists"
    top_area, score = result.relevant_areas[0]
    assert score > 0
    assert "explored_bill" in top_area.related_flow_ids or "billing" in top_area.categories
