"""Knowledge context retrieval and staleness tracking."""

from __future__ import annotations

import pytest

from navigator.app.registry import Registry, NewProduct
from navigator.knowledge.context import retrieve_context
from navigator.knowledge.ingest import ingest_knowledge_text, _chunk_text


def test_semantic_chunking():
    """Text is chunked by paragraphs, preserving structure."""
    text = "Para A.\n\nPara B.\n\nPara C.\n\nPara D.\n\nPara E.\n\nPara F."
    # Very low target forces break between paragraphs
    chunks = _chunk_text(text, target_tokens=1)
    assert len(chunks) >= 2, f"expected ≥2 chunks with target=1, got {len(chunks)}"
    # Each chunk is non-empty
    for chunk in chunks:
        assert chunk.strip()
    # Chunks don't split mid-paragraph (no bare "Para" halfway through)
    for chunk in chunks:
        assert chunk.count("Para") == len(chunk.split("\n\n"))


def test_dedup_same_chunk():
    """Re-ingesting the same text doesn't create duplicates."""
    text = "Important billing information about invoice handling and payment processing."
    product_id = "test-dedup"

    tmp = __import__("tempfile").TemporaryDirectory()
    try:
        ids_1 = ingest_knowledge_text(text, product_id, chroma_path=tmp.name)
        ids_2 = ingest_knowledge_text(text, product_id, chroma_path=tmp.name)

        assert ids_1 == ids_2, "same text should produce same chunk IDs"
        # Verify Chroma doesn't have duplicates
        from navigator.knowledge.memory.collections import get_collection

        coll = get_collection(tmp.name, product_id, "product_knowledge")
        assert coll.count() == 1, f"expected 1 chunk in Chroma, got {coll.count()}"
    finally:
        tmp.cleanup()


def test_retrieval_with_staleness(tmp_path):
    """Knowledge tied to old revision is flagged as stale."""
    registry = Registry(tmp_path / "registry.db")

    # Register product, retrieve revision before any upload
    spec = NewProduct(name="Test", product_id="test-stale")
    prod = registry.register(spec)
    product_id = prod.product.product_id

    # No revision published yet
    with pytest.raises(Exception):  # ProductNotFound
        registry.published_revision(product_id)

    # Upload a site graph, publish as revision 1
    from navigator.knowledge.site_graph import parse_site_graph

    yaml = """version: 1
site: test-stale
base_url: https://example.com
pages:
  main:
    name: Main
    url: /
    selectors:
      body: body
    flows: {}
"""
    registry.put_site_graph(product_id, yaml, publish=True)

    # Ingest knowledge "tied to" revision 1
    text = "Learn about contacts and customer management features."
    ingest_knowledge_text(
        text,
        product_id,
        revision_tied_to=1,
        chroma_path=tmp_path / "chroma",
    )

    # Verify not stale yet
    result = retrieve_context(
        "how to manage contacts",
        product_id,
        registry=registry,
        chroma_path=tmp_path / "chroma",
    )
    assert not result.is_stale, "knowledge tied to current revision"
    assert result.current_published_revision == 1

    # Upload a new revision, publish it
    yaml2 = yaml.replace("version: 1", "version: 2")
    registry.put_site_graph(product_id, yaml2, publish=True)

    # Now knowledge (still tied to rev 1) is stale
    result = retrieve_context(
        "how to manage contacts",
        product_id,
        registry=registry,
        chroma_path=tmp_path / "chroma",
    )
    assert result.is_stale, "knowledge from old revision should be stale"
    assert result.knowledge_based_on_revision == 1
    assert result.current_published_revision == 2


def test_retrieval_returns_all_contexts(tmp_path):
    """Single retrieve_context call returns knowledge, flows, staleness."""
    registry = Registry(tmp_path / "registry.db")
    spec = NewProduct(name="Multi", product_id="test-multi")
    prod = registry.register(spec)
    product_id = prod.product.product_id

    # Ingest knowledge
    text = """
Billing: Track invoices and payments for customers.
Contacts: Manage customer contact information and communication history.
Reporting: Generate reports on sales, pipeline, and team performance.
"""
    chunk_ids = ingest_knowledge_text(
        text, product_id, revision_tied_to=1, chroma_path=tmp_path / "chroma"
    )
    assert len(chunk_ids) >= 1

    # Retrieve all at once
    result = retrieve_context(
        "How do I invoice a customer?",
        product_id,
        available_flow_ids=["flow_invoice", "flow_payment"],
        registry=registry,
        chroma_path=tmp_path / "chroma",
    )

    assert result.has_knowledge, "should have knowledge chunks"
    assert result.has_flows, "should have candidate flows"
    assert len(result.knowledge_chunks) >= 1
    assert len(result.candidate_flows) == 2
    assert not result.is_stale, "revision 1 is current"


def test_retrieval_empty_product(tmp_path):
    """Retrieve on empty product returns graceful empty result."""
    registry = Registry(tmp_path / "registry.db")
    spec = NewProduct(name="Empty", product_id="test-empty")
    prod = registry.register(spec)
    product_id = prod.product.product_id

    result = retrieve_context(
        "any query",
        product_id,
        available_flow_ids=["flow_a"],
        registry=registry,
        chroma_path=tmp_path / "chroma",
    )

    assert not result.has_knowledge
    assert result.has_flows
    assert result.is_stale is False
