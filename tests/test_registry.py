"""Product registry: revisions are immutable, uploads validate before storing."""

from __future__ import annotations

import textwrap

import pytest

from navigator.app.registry import (
    NewProduct,
    ProductNotFound,
    Registry,
    RegistryError,
    hash_key,
)
from navigator.knowledge.site_graph import SiteGraphError

GRAPH = textwrap.dedent(
    """
    version: 1
    site: acme-inbox
    base_url: https://app.acme.test/
    persona:
      product_name: Acme Inbox
      one_liner: shared inbox for support teams
    pages:
      inbox:
        name: Inbox
        url: inbox
        selectors:
          send_button: "[data-nav='send_button']"
          bubble: ".sent"
        flows:
          send:
            - tool: click_element
              selector: send_button
              expects: {check: visible, selector: bubble}
    """
)


@pytest.fixture
def registry(tmp_path):
    with Registry(tmp_path / "registry.db") as r:
        yield r


@pytest.fixture
def product(registry):
    return registry.register(NewProduct(name="Acme Inbox"))


def test_register_derives_a_slug_and_returns_a_key_once(registry):
    reg = registry.register(NewProduct(name="Acme Inbox"))
    assert reg.product.product_id == "acme-inbox"
    assert reg.api_key.startswith("nav_")
    assert reg.product.active_revision is None


def test_api_key_is_stored_hashed_not_plaintext(registry, tmp_path):
    reg = registry.register(NewProduct(name="Secret Co"))
    # WAL mode means a fresh write may live in -wal rather than the main file.
    raw = b"".join(p.read_bytes() for p in sorted(tmp_path.glob("registry.db*")))
    assert reg.api_key.encode() not in raw, "plaintext key must never hit disk"
    assert hash_key(reg.api_key).encode() in raw


def test_authenticate_round_trips(registry):
    reg = registry.register(NewProduct(name="Acme"))
    assert registry.authenticate(reg.api_key).product_id == reg.product.product_id


def test_authenticate_rejects_unknown_key(registry):
    with pytest.raises(ProductNotFound):
        registry.authenticate("nav_nope")


def test_duplicate_product_id_rejected(registry):
    registry.register(NewProduct(name="Acme Inbox"))
    with pytest.raises(RegistryError, match="already registered"):
        registry.register(NewProduct(name="Acme Inbox"))


def test_explicit_product_id_is_honoured(registry):
    reg = registry.register(NewProduct(name="Acme Inbox", product_id="acme_v2"))
    assert reg.product.product_id == "acme_v2"


# --- site graph revisions ----------------------------------------------------


def test_upload_becomes_revision_one_and_activates(registry, product):
    rev = registry.put_site_graph(product.product.product_id, GRAPH, publish=True)
    assert rev.revision == 1
    assert rev.source == "yaml"
    assert rev.site == "acme-inbox"
    assert registry.get(product.product.product_id).active_revision == 1


def test_uploads_never_overwrite(registry, product):
    pid = product.product.product_id
    registry.put_site_graph(pid, GRAPH, publish=True)
    second = registry.put_site_graph(
        pid, GRAPH.replace("version: 1", "version: 2"), publish=True
    )

    assert second.revision == 2
    assert len(registry.revisions(pid)) == 2
    assert registry.get_revision(pid, 1).graph_version == 1, "history is preserved"
    assert registry.get_revision(pid).graph_version == 2, "active is the newest"


def test_invalid_upload_is_rejected_and_leaves_active_alone(registry, product):
    """A customer must not be able to break a live demo with a bad push."""
    pid = product.product.product_id
    registry.put_site_graph(pid, GRAPH, publish=True)

    broken = GRAPH.replace("selector: send_button", "selector: ghost")
    with pytest.raises(SiteGraphError, match="unknown selector 'ghost'"):
        registry.put_site_graph(pid, broken, publish=True)

    assert registry.get(pid).active_revision == 1
    assert len(registry.revisions(pid)) == 1, "nothing stored on rejection"


def test_upload_requires_an_absolute_base_url(registry, product):
    """An upload has no directory to resolve against; guessing would be a
    path-traversal foothold."""
    relative = GRAPH.replace("https://app.acme.test/", "../../etc/")
    with pytest.raises(SiteGraphError, match="must be absolute"):
        registry.put_site_graph(product.product.product_id, relative, publish=True)


def test_source_is_recorded_for_provenance(registry, product):
    rev = registry.put_site_graph(
        product.product.product_id, GRAPH, source="sdk", publish=True
    )
    assert rev.source == "sdk"
    assert registry.get_revision(product.product.product_id).source == "sdk"


def test_load_graph_returns_a_usable_site_graph(registry, product):
    pid = product.product.product_id
    registry.put_site_graph(pid, GRAPH, publish=True)
    graph = registry.load_graph(pid)

    assert graph.selector("inbox", "send_button") == "[data-nav='send_button']"
    assert graph.url_for("inbox") == "https://app.acme.test/inbox"
    assert graph.effective_persona().product_name == "Acme Inbox"


def test_activate_rolls_back_to_an_older_revision(registry, product):
    pid = product.product.product_id
    registry.put_site_graph(pid, GRAPH, publish=True)
    registry.put_site_graph(
        pid, GRAPH.replace("version: 1", "version: 7"), publish=True
    )
    assert registry.load_graph(pid).version == 7

    registry.activate(pid, 1)
    assert registry.load_graph(pid).version == 1


# --- drafts: an unpublished revision must not reach End Users ----------------


def test_an_unpublished_upload_does_not_change_what_is_live(registry, product):
    pid = product.product.product_id
    registry.put_site_graph(pid, GRAPH, publish=True)
    draft = registry.put_site_graph(
        pid, GRAPH.replace("version: 1", "version: 9"), publish=False
    )

    assert draft.revision == 2 and draft.published is False
    assert registry.published_revision(pid) == 1
    assert registry.load_graph(pid).version == 1, "live traffic keeps revision 1"
    assert registry.latest_revision(pid).revision == 2, "the draft is what's edited"


def test_publishing_a_draft_makes_it_live(registry, product):
    pid = product.product.product_id
    registry.put_site_graph(pid, GRAPH, publish=True)
    registry.put_site_graph(
        pid, GRAPH.replace("version: 1", "version: 9"), publish=False
    )

    registry.activate(pid, 2)

    assert registry.published_revision(pid) == 2
    assert registry.load_graph(pid).version == 9
    assert registry.get_revision(pid, 2).published is True


def test_a_product_with_only_drafts_has_nothing_to_serve_live(registry, product):
    pid = product.product.product_id
    registry.put_site_graph(pid, GRAPH, publish=False)

    with pytest.raises(ProductNotFound, match="no published site graph"):
        registry.published_revision(pid)


def test_activate_rejects_a_missing_revision(registry, product):
    with pytest.raises(ProductNotFound):
        registry.activate(product.product.product_id, 99)


def test_demo_before_any_upload_is_an_error(registry, product):
    with pytest.raises(ProductNotFound, match="no site graph yet"):
        registry.load_graph(product.product.product_id)


def test_upload_to_unknown_product_is_an_error(registry):
    with pytest.raises(ProductNotFound):
        registry.put_site_graph("ghost-product", GRAPH, publish=True)


def test_persona_defaults_from_site_when_absent(registry, product):
    no_persona = "\n".join(
        line
        for line in GRAPH.splitlines()
        if not line.startswith(("persona:", "  product_name:", "  one_liner:"))
    )
    registry.put_site_graph(product.product.product_id, no_persona, publish=True)
    persona = registry.load_graph(product.product.product_id).effective_persona()
    assert persona.product_name == "acme inbox"
