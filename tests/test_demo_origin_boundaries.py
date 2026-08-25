"""The three-role boundary: who may start which demo, against which revision.

See docs/PRODUCT_MODEL.md. Four rules are load-bearing enough to pin down here:

  1. a live demo needs an End User credential -- a dashboard JWT will not do
  2. a test demo needs Client dashboard auth
  3. a live demo runs the published revision even when a newer draft exists
  4. usage aggregation counts live sessions and ignores test ones
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from navigator.logs.store import ActionLog
from test_action_log import TS, entry
from test_api import ACME_LIVE as ACME, register
from test_client_dashboard import _client, _cleanup


@pytest.fixture
def bundle(tmp_path):
    b = _client(tmp_path, client_api_key="")
    yield b
    _cleanup(b, "")


def _dashboard_headers(client, auth_store, product_id: str) -> dict:
    auth_store.create_user(
        product_id=product_id, email="client@acme.com", password="password"
    )
    r = client.post(
        "/v1/auth/login",
        json={"email": "client@acme.com", "password": "password"},
        headers={"Host": "localhost"},
    )
    assert r.status_code == 200, r.text
    return {"Host": "localhost", "Authorization": f"Bearer {r.json()['access_token']}"}


# --- who may start what ------------------------------------------------------


def test_a_live_demo_needs_an_end_user_credential(bundle):
    """The public route is for End Users. No credential, no demo."""
    client, *_ = bundle

    assert client.post("/v1/demos/start", json={}).status_code == 401
    assert (
        client.post(
            "/v1/demos/start",
            headers={"Authorization": "Token sess_forged"},
            json={},
        ).status_code
        == 401
    )


def test_a_dashboard_jwt_cannot_start_a_live_demo(bundle):
    """A Client testing their own setup must not be able to bill themselves as
    live traffic by pointing their dashboard token at the public route."""
    client, _prev, _registry, _log, auth_store = bundle
    p = register(client, "Acme Inbox", ACME)
    headers = _dashboard_headers(client, auth_store, p["id"])

    r = client.post("/v1/demos/start", headers=headers, json={})
    assert r.status_code == 401


def test_a_session_token_starts_a_demo_marked_live(bundle):
    client, *_ = bundle
    p = register(client, "Acme Inbox", ACME)
    token = client.post(
        "/v1/session-tokens", headers=p["headers"], json={}
    ).json()["token"]

    r = client.post(
        "/v1/demos/start",
        headers={"Authorization": f"Token {token}"},
        json={"page_id": "main", "flow_id": "happy_path"},
    )
    assert r.status_code == 202, r.text
    assert r.json()["origin"] == "public_embed"


def test_a_test_demo_needs_client_dashboard_auth(bundle):
    client, *_ = bundle
    register(client, "Acme Inbox", ACME)

    r = client.post(
        "/client/api/demos/start", headers={"Host": "localhost"}, json={}
    )
    assert r.status_code == 401


def test_a_dashboard_demo_is_marked_as_a_test(bundle):
    client, _prev, _registry, _log, auth_store = bundle
    p = register(client, "Acme Inbox", ACME)
    headers = _dashboard_headers(client, auth_store, p["id"])

    r = client.post(
        "/client/api/demos/start",
        headers=headers,
        json={"page_id": "main", "flow_id": "happy_path"},
    )
    assert r.status_code == 202, r.text
    assert r.json()["origin"] == "dashboard_test"


# --- which revision a demo runs ----------------------------------------------


def test_a_live_demo_ignores_a_newer_draft(bundle):
    """An End User must never be shown a half-finished revision the Client is
    still editing."""
    client, _prev, registry, _log, auth_store = bundle
    p = register(client, "Acme Inbox", ACME)
    headers = _dashboard_headers(client, auth_store, p["id"])

    draft = client.put(
        "/client/api/site-graph",
        headers=headers,
        json={"yaml": ACME.replace("version: 1", "version: 7")},
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["revision"] == 2
    assert registry.published_revision(p["id"]) == 1

    token = client.post(
        "/v1/session-tokens", headers=p["headers"], json={}
    ).json()["token"]
    live = client.post(
        "/v1/demos/start",
        headers={"Authorization": f"Token {token}"},
        json={"page_id": "main", "flow_id": "happy_path"},
    )
    assert live.status_code == 202, live.text
    assert live.json()["revision"] == 1, "live traffic stays on the published revision"

    published = client.post("/client/api/site-graph/publish", headers=headers, json={})
    assert published.json()["published_revision"] == 2
    assert registry.published_revision(p["id"]) == 2


def test_a_test_demo_runs_the_draft(bundle):
    """Validating a draft before publishing is the whole point of a test demo."""
    client, _prev, _registry, _log, auth_store = bundle
    p = register(client, "Acme Inbox", ACME)
    headers = _dashboard_headers(client, auth_store, p["id"])

    client.put(
        "/client/api/site-graph",
        headers=headers,
        json={"yaml": ACME.replace("version: 1", "version: 7")},
    )

    r = client.post(
        "/client/api/demos/start",
        headers=headers,
        json={"page_id": "main", "flow_id": "happy_path"},
    )
    assert r.status_code == 202, r.text
    assert r.json()["revision"] == 2


# --- billing ------------------------------------------------------------------


def test_usage_aggregation_ignores_test_sessions(tmp_path):
    live_sid, test_sid = uuid4(), uuid4()

    with ActionLog(tmp_path / "a.db") as log:
        for sid, origin in ((live_sid, "public_embed"), (test_sid, "dashboard_test")):
            log.upsert_run(
                session_id=sid,
                demo_id=uuid4(),
                product_id="acme",
                platform="static",
                status="finished",
                origin=origin,
                started_at=TS,
            )
            action = entry(sid)
            action.product_id = "acme"
            log.append(action)

        m = log.product_metrics("acme")

    assert m["sessions"] == 1, "only the End User's session is billable"
    assert m["actions"] == 1
    assert m["test_sessions"] == 1, "the Client's test run is still reported"
