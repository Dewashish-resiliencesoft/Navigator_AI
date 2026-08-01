"""Chroma collections and retrieval, namespaced per product."""

from __future__ import annotations

from navigator.knowledge.memory.collections import collection_name, get_collection
from navigator.knowledge.memory.retrieval import retrieve_corrections, retrieve_product_knowledge
from navigator.knowledge.memory.seed import seed_correction, seed_knowledge


def test_collection_name_short_product():
    assert collection_name("acme", "corrections") == "acme_corr"
    assert collection_name("acme", "product_knowledge") == "acme_kb"


def test_collection_name_long_product_stays_under_63_and_stable():
    long_id = "a" * 80
    name = collection_name(long_id, "corrections")
    assert len(name) <= 63
    assert name.endswith("_corr")
    assert collection_name(long_id, "corrections") == name


def test_get_collection_creates_namespaced_collection(tmp_path):
    path = tmp_path / "chroma"
    coll = get_collection(path, "acme", "corrections")
    assert coll.name == "acme_corr"
    other = get_collection(path, "beta", "corrections")
    assert other.name == "beta_corr"


def test_retrieve_corrections_filters_by_page_and_tenant(tmp_path):
    path = tmp_path / "chroma"

    seed_correction(
        path,
        product_id="acme",
        rule="Click send only after the composer is focused",
        page="inbox",
        tool_call_type="click_element",
        source_call_id="call-1",
    )
    seed_correction(
        path,
        product_id="acme",
        rule="Settings save needs a wait_for on toast",
        page="settings",
        tool_call_type="click_element",
        source_call_id="call-2",
    )
    seed_correction(
        path,
        product_id="other",
        rule="SECRET other tenant rule",
        page="inbox",
        tool_call_type="click_element",
        source_call_id="call-3",
    )

    hits = retrieve_corrections(
        "acme",
        query="send message",
        page="inbox",
        tool_call_type="click_element",
        k=5,
        path=path,
    )
    assert len(hits) == 1
    assert hits[0].rule.startswith("Click send")
    assert hits[0].product_id == "acme"
    assert all(h.product_id == "acme" for h in hits)


def test_retrieve_product_knowledge_returns_docs(tmp_path):
    path = tmp_path / "chroma"
    seed_knowledge(
        path, product_id="acme", text="Inbox is the shared WhatsApp thread list"
    )
    docs = retrieve_product_knowledge(
        "acme", query="whatsapp inbox", k=3, path=path
    )
    assert docs
    assert "Inbox" in docs[0]


def test_retrieve_empty_collection_returns_empty(tmp_path):
    path = tmp_path / "chroma"
    assert retrieve_corrections("acme", "q", page="inbox", path=path) == []
    assert retrieve_product_knowledge("acme", "q", path=path) == []
