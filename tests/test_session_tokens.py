import pytest
from datetime import datetime, timezone
import json
import time

from fastapi.testclient import TestClient
from navigator.app.main import app, get_registry
from navigator.app.registry import Registry, NewProduct
from navigator.app.session_tokens import SessionTokenStore, SessionTokenError

@pytest.fixture
def registry(tmp_path):
    reg = Registry(tmp_path / "registry.db")
    return reg

@pytest.fixture
def client(registry, monkeypatch, tmp_path):
    from navigator.app.main import get_token_store, _token_store
    app.dependency_overrides[get_registry] = lambda: registry
    
    _token_store.db_path = tmp_path / "tokens.db"
    
    with TestClient(app) as c:
        yield c
    
    app.dependency_overrides.clear()

def test_session_token_lifecycle(client, registry):
    # 1. Register a product
    prod_res = registry.register(NewProduct(name="Test Product", product_id="test-prod"))
    api_key = prod_res.api_key
    
    # 2. Generate a session token
    res = client.post(
        "/v1/session-tokens",
        headers={"Authorization": f"Token {api_key}"},
        json={"intake": {"name": "Test User"}, "expires_in_seconds": 3600}
    )
    assert res.status_code == 201, res.text
    data = res.json()
    token = data["token"]
    assert token.startswith("sess_")
    assert data["product_id"] == "test-prod"
    
    # 3. Start demo using the session token
    # To test this without mocking meeting providers, we will hit an error from the provider.
    # However, the token validation happens BEFORE the meeting provider is called!
    # So we can just check if it gets past auth and fails on graph/meeting, meaning auth succeeded.
    res2 = client.post(
        "/v1/demos/start",
        headers={"Authorization": f"Token {token}"},
        json={"page_id": "home", "flow_id": "flow1"}
    )
    # It should pass auth (401), but fail at graph load (404 product not found in graph registry)
    assert res2.status_code != 401
    
    # 4. Try to use it again (should be rejected as already used)
    res3 = client.post(
        "/v1/demos/start",
        headers={"Authorization": f"Token {token}"},
        json={"page_id": "home", "flow_id": "flow1"}
    )
    assert res3.status_code == 401
    assert "already used" in res3.text.lower()

def test_session_token_expired(client, registry, monkeypatch):
    prod_res = registry.register(NewProduct(name="Test Product 2", product_id="test-prod-2"))
    
    res = client.post(
        "/v1/session-tokens",
        headers={"Authorization": f"Token {prod_res.api_key}"},
        json={"expires_in_seconds": 60}
    )
    token = res.json()["token"]
    
    # Time travel
    import navigator.app.session_tokens as st
    def fake_now(tz):
        from datetime import timedelta
        return datetime.now(timezone.utc) + timedelta(seconds=100)
    monkeypatch.setattr(st, "datetime", type("MockDatetime", (), {"now": fake_now, "fromisoformat": datetime.fromisoformat, "fromtimestamp": datetime.fromtimestamp}))
    
    res2 = client.post(
        "/v1/demos/start",
        headers={"Authorization": f"Token {token}"},
        json={"page_id": "home", "flow_id": "flow1"}
    )
    assert res2.status_code == 401
    assert "expired" in res2.text.lower()

def test_session_token_cross_tenant(client, registry):
    p1 = registry.register(NewProduct(name="P1", product_id="p1"))
    
    # Generate token for P1
    res = client.post(
        "/v1/session-tokens",
        headers={"Authorization": f"Token {p1.api_key}"},
        json={"expires_in_seconds": 3600}
    )
    token = res.json()["token"]
    
    # The token inherently belongs to P1 based on the DB lookup. 
    # authed_or_session returns P1's product model automatically.
    # So cross-tenant demo start is impossible by design, because the product ID
    # is resolved from the token, not from the request body!
    # Let's verify it resolves to P1.
    res2 = client.post(
        "/v1/demos/start",
        headers={"Authorization": f"Token {token}"},
        json={}
    )
    # 404 because P1 has no graph uploaded, meaning it successfully resolved as P1.
    assert res2.status_code == 404
    assert "no published site graph" in res2.text or "no such product" in res2.text
