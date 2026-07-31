"""The mechanism behind "the docs update with every change".

test_committed_docs_are_current is the whole enforcement. Add an endpoint, rename a
field, add a postcondition kind, forget to rebuild -- this goes red and names the
command. No git hook, no CI config, nothing bypassable with --no-verify.

The rest of the tests are here so that when it does go red, the failure is
actionable: they pin the properties that made the generated docs worth trusting.
"""

from __future__ import annotations

import json
from typing import get_args

import pytest
import yaml

from navigator.docs import build as build_mod
from navigator.docs.model import build as build_model, endpoints_from_openapi
from navigator.docs.snippets import SNIPPETS
from navigator.schemas import CheckKind


@pytest.fixture(scope="module")
def model():
    return build_model()


@pytest.fixture(scope="module")
def generated():
    return build_mod.artifacts()


# --- the enforcement ---------------------------------------------------------


def test_committed_docs_are_current():
    stale = build_mod.stale()
    assert not stale, (
        "Committed documentation is out of date:\n  "
        + "\n  ".join(str(p) for p in stale)
        + "\n\nRegenerate with: python -m navigator.docs build"
    )


def test_check_command_agrees_with_stale(capsys):
    assert build_mod.main(["check"]) == 0
    assert "up to date" in capsys.readouterr().out


def test_build_is_deterministic():
    """Two builds must be byte-identical, or `check` would flap in CI."""
    assert build_mod.artifacts() == build_mod.artifacts()


def test_stale_detects_a_hand_edit(tmp_path):
    for rel, content in build_mod.artifacts().items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    assert build_mod.stale(tmp_path) == []

    (tmp_path / build_mod.HTML_OUT).write_text("edited by hand\n")
    assert build_mod.stale(tmp_path) == [build_mod.HTML_OUT]


def test_write_creates_every_artifact(tmp_path):
    written = build_mod.write(tmp_path)

    assert set(written) == set(build_mod.artifacts())
    for rel in written:
        assert (tmp_path / rel).exists(), rel
    # Nested output dirs are created, not assumed.
    assert (tmp_path / "fern/openapi/openapi.yml").exists()
    assert (tmp_path / "fern/pages/integration.mdx").exists()


# --- the API reference is the server's own schema -----------------------------


def test_openapi_artifact_round_trips_to_the_live_schema(generated, model):
    spec = yaml.safe_load(generated[build_mod.OPENAPI_OUT])

    # Not "looks similar" -- identical. Anything less and Fern's SDK could
    # describe endpoints the server doesn't serve.
    assert spec == json.loads(json.dumps(model.openapi))


def test_every_route_reaches_the_html(generated, model):
    html = generated[build_mod.HTML_OUT]
    assert model.endpoints, "no endpoints extracted; the app failed to import?"

    for ep in model.endpoints:
        assert f'id="{ep.anchor}"' in html, f"{ep.method} {ep.path} missing from HTML"


def test_html_endpoint_count_matches_openapi(generated, model):
    operations = sum(
        1
        for methods in model.openapi["paths"].values()
        for verb in methods
        if verb.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}
    )
    assert len(model.endpoints) == operations
    assert generated[build_mod.HTML_OUT].count('class="ep"') == operations


def test_registration_and_healthz_are_marked_unauthed(model):
    by_key = {(e.method, e.path): e for e in model.endpoints}
    assert by_key[("POST", "/v1/products")].authed is False
    assert by_key[("GET", "/healthz")].authed is False
    # Everything else needs a key, and the docs must say so.
    for (method, path), ep in by_key.items():
        if path not in {"/healthz", "/v1/products"}:
            assert ep.authed, f"{method} {path} documented as public"


def test_auth_header_is_not_repeated_per_route(model):
    """Documented once, globally -- a header on all 13 routes is noise."""
    for ep in model.endpoints:
        assert not any(p.name.lower() == "authorization" for p in ep.params)


def test_request_examples_are_valid_json(model):
    for ep in model.endpoints:
        if ep.request_example:
            json.loads(ep.request_example)


def test_anchors_are_unique(model):
    anchors = [e.anchor for e in model.endpoints]
    assert len(anchors) == len(set(anchors))


def test_endpoints_from_openapi_skips_non_operations():
    spec = {
        "paths": {
            "/x": {
                "get": {"summary": "x", "responses": {"200": {}}},
                "parameters": [],  # a path-level key, not an operation
            }
        }
    }
    eps = endpoints_from_openapi(spec)
    assert [e.method for e in eps] == ["GET"]


# --- schema tables track the actual types ------------------------------------


def test_every_check_kind_is_documented(model, generated):
    kinds = get_args(CheckKind)
    assert [k for k, _ in model.check_kinds] == list(kinds)

    for kind, description in model.check_kinds:
        assert description, f"CheckKind {kind!r} has no description in CHECK_DESCRIPTIONS"
        assert f"<code>{kind}</code>" in generated[build_mod.HTML_OUT]
        assert f"`{kind}`" in generated[build_mod.MDX_OUT]


def test_every_tool_and_its_fields_are_documented(model, generated):
    html = generated[build_mod.HTML_OUT]
    mdx = generated[build_mod.MDX_OUT]
    assert len(model.tools) == 4

    for tool in model.tools:
        assert f"<h3>{tool.name}</h3>" in html
        assert f"#### {tool.name}" in mdx
        for field in tool.fields:
            assert f"<code>{field.name}</code>" in html
            assert f"`{field.name}`" in mdx


def test_site_graph_fields_are_documented(model, generated):
    names = {m.name for m in model.models}
    assert {"SiteGraph", "PageSpec", "Persona", "Postcondition"} <= names

    html = generated[build_mod.HTML_OUT]
    for doc in model.models:
        assert f'id="model-{doc.name.lower()}"' in html
        for field in doc.fields:
            assert f"<code>{field.name}</code>" in html


def test_required_and_optional_are_distinguished(model):
    site_graph = next(m for m in model.models if m.name == "SiteGraph")
    fields = {f.name: f for f in site_graph.fields}

    assert fields["version"].required and fields["version"].default == "required"
    assert not fields["persona"].required


# --- Fern project -------------------------------------------------------------


def test_fern_config_is_valid_json(generated):
    config = json.loads(generated[build_mod.FERN_CONFIG_OUT])
    assert config["organization"] == build_mod.ORGANIZATION


def test_org_slug_is_written_in_exactly_one_place(generated):
    """A rename that updates fern.config.json but not the instance URL publishes
    to a subdomain the org doesn't own, and the failure is a 403 from Fern rather
    than anything local. So derive one from the other."""
    config = json.loads(generated[build_mod.FERN_CONFIG_OUT])
    docs = yaml.safe_load(generated[build_mod.DOCS_YML_OUT])

    assert build_mod.DOCS_INSTANCE.startswith(f"{config['organization']}.")
    assert docs["instances"] == [{"url": build_mod.DOCS_INSTANCE}]


def test_generators_points_at_the_generated_spec(generated):
    generators = yaml.safe_load(generated[build_mod.GENERATORS_OUT])

    assert generators["api"]["specs"] == [{"openapi": "./openapi/openapi.yml"}]
    assert generators["default-group"] in generators["groups"]
    # The auth scheme must match what the server actually accepts.
    assert generators["auth-schemes"]["token"]["header"] == "Authorization"
    assert generators["auth-schemes"]["token"]["prefix"] == "Token "


def test_docs_yml_references_a_page_we_generate(generated):
    docs = yaml.safe_load(generated[build_mod.DOCS_YML_OUT])
    paths = {
        item["path"]
        for entry in docs["navigation"]
        for item in entry.get("contents", [])
        if "path" in item
    }

    # A docs.yml pointing at a file the generator doesn't write fails `fern check`.
    assert paths == {str(build_mod.MDX_OUT.relative_to("fern"))}
    assert any("api" in entry for entry in docs["navigation"])


def test_docs_yml_does_not_fetch_the_spec_from_a_url(generated):
    """Fern resolves the spec from generators.yml, so docs builds need no deployment."""
    docs = yaml.safe_load(generated[build_mod.DOCS_YML_OUT])
    api_entry = next(e for e in docs["navigation"] if "api" in e)

    assert "openapi" not in api_entry
    assert "://" not in json.dumps(api_entry)


# --- snippets ----------------------------------------------------------------


def test_every_snippet_is_used(generated):
    """A snippet nobody renders is dead prose that still has to be maintained."""
    from html import escape

    html = generated[build_mod.HTML_OUT]
    mdx = generated[build_mod.MDX_OUT]

    for name, body in SNIPPETS.items():
        first = body.strip().splitlines()[0]
        assert first in mdx, f"snippet {name} missing from the Fern page"
        assert escape(first, quote=False) in html, f"snippet {name} missing from the HTML"


def test_snippet_yaml_examples_parse():
    for name in ("site_graph_yaml", "ci_yaml"):
        assert yaml.safe_load(SNIPPETS[name]), name


def test_documented_site_graph_example_actually_validates():
    """The YAML a customer copies out of the docs must pass the real validator.

    A docs example that the server rejects is worse than no example.
    """
    from navigator.config.site_graph import parse_site_graph

    graph = parse_site_graph(SNIPPETS["site_graph_yaml"], origin="docs snippet")

    assert graph.site == "acme-inbox"
    assert graph.effective_persona().product_name == "Acme Inbox"
    assert len(graph.flow("inbox", "send_message")) == 3


def test_docs_snippet_curl_uses_the_documented_paths(model):
    paths = {e.path for e in model.endpoints}
    curl = SNIPPETS["quickstart_curl"]

    for path in ("/v1/products", "/v1/products/site-graph", "/v1/demos"):
        assert path in paths, f"{path} is no longer a route; the quickstart is wrong"
        assert path in curl


# --- CLI ---------------------------------------------------------------------


def test_check_exits_nonzero_when_stale(tmp_path, capsys):
    assert build_mod.main(["check", "--root", str(tmp_path)]) == 1

    err = capsys.readouterr().err
    assert "stale documentation" in err
    # The failure must carry its own fix.
    assert "python -m navigator.docs build" in err


def test_build_then_check_is_clean(tmp_path, capsys):
    assert build_mod.main(["build", "--root", str(tmp_path)]) == 0
    capsys.readouterr()
    assert build_mod.main(["check", "--root", str(tmp_path)]) == 0
